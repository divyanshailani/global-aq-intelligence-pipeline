#!/usr/bin/env python3
"""Bounded recent weather repair before the unchanged written-data contract.
Forecast endpoint only, including GB. Finite cells are immutable. Fail closed.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import re,json,datetime,math,sys,time,subprocess,hashlib,os
from pathlib import Path
import requests,psycopg2
from psycopg2.extras import execute_values
from src.config import DB_CONFIG
root=Path('/tmp/recent-weather-recovery');root.mkdir(exist_ok=True)
ledger=root/'ledger.jsonl'
def conn():
 c=psycopg2.connect(**DB_CONFIG,connect_timeout=10,keepalives=1,keepalives_idle=30,keepalives_interval=10,keepalives_count=3)
 with c.cursor() as q:q.execute('SET statement_timeout=60000')
 c.commit();return c
fields=['om_temperature','om_wind_speed','om_precipitation','om_aerosol_optical_depth']
def missing(v):return v is None or isinstance(v,float) and math.isnan(v)
def guard(c):
 with c.cursor() as q:
  q.execute('SELECT pg_database_size(current_database())');assert q.fetchone()[0]<28*1024**3
  q.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state='active' AND pid<>pg_backend_pid() AND query NOT ILIKE 'SELECT%'");assert q.fetchone()[0]==0,'writer active'
 c.commit()
def record(x):
 x['at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with ledger.open('a') as f:f.write(json.dumps(x,default=str)+'\n')
def fetch(kind,coords,dt):
 today=datetime.datetime.now(datetime.timezone.utc).date();assert today-datetime.timedelta(days=5)<=dt<=today
 assert sum(x.get('api_calls',0) for x in map(json.loads,ledger.read_text().splitlines() if ledger.exists() else []) if str(x.get('at','')).startswith(today.isoformat()))<6000
 url='https://api.open-meteo.com/v1/forecast' if kind=='weather' else 'https://air-quality-api.open-meteo.com/v1/air-quality'
 params=dict(latitude=','.join(str(x[0]) for x in coords),longitude=','.join(str(x[1]) for x in coords),start_date=str(dt),end_date=str(dt),timezone='auto')
 params.update({'daily':'temperature_2m_mean,wind_speed_10m_max,precipitation_sum'} if kind=='weather' else {'hourly':'aerosol_optical_depth'})
 t=time.monotonic()
 try:r=requests.get(url,params=params,timeout=(10,35))
 except requests.RequestException as e:
  record(dict(kind='daily_recent_recovery_'+kind,api_calls=1,error=type(e).__name__,date=str(dt),locations=len(coords)));raise
 record(dict(kind='daily_recent_recovery_'+kind,api_calls=1,http=r.status_code,date=str(dt),locations=len(coords),seconds=round(time.monotonic()-t,2)))
 print('SOURCE',kind,dt,len(coords),r.status_code,round(time.monotonic()-t,2),flush=True)
 if r.status_code!=200:raise RuntimeError(str(r.status_code)+' '+r.text[:200])
 entries=r.json();assert isinstance(entries,list) and len(entries)==len(coords)
 (root/(str(dt)+'-'+kind+'-'+hashlib.sha256(repr(coords).encode()).hexdigest()[:12]+'.json')).write_bytes(r.content)
 out=[]
 for a in entries:
  if kind=='weather':
   d=a['daily'];assert d['time']==[str(dt)]
   vals=[d[k][0] for k in ['temperature_2m_mean','wind_speed_10m_max','precipitation_sum']]
   assert all(v is not None and math.isfinite(v) for v in vals),'weather source incomplete'
   out.append(vals)
  else:
   h=a['hourly'];assert h['time'] and all(x.startswith(str(dt)) for x in h['time'])
   vals=[v for v in h['aerosol_optical_depth'] if v is not None and math.isfinite(v)]
   out.append(sum(vals)/len(vals) if vals else None)
 return out
c=conn();guard(c)
today=datetime.datetime.now(datetime.timezone.utc).date();lo=today-datetime.timedelta(days=5)
deadline=time.monotonic()+600
repaired_rows=0
for _ in range(125):
 if time.monotonic()>deadline:raise RuntimeError('recent repair 10-minute budget exhausted')
 with c.cursor() as q:
  q.execute("SELECT DISTINCT d.station_id,d.date,s.latitude,s.longitude FROM daily_features d JOIN stations s ON s.id=d.station_id WHERE d.date BETWEEN %s AND %s AND s.country_code IN ('IN','US','GB','AU') AND s.latitude IS NOT NULL AND s.longitude IS NOT NULL AND (d.om_temperature IS NULL OR d.om_temperature::text='NaN' OR d.om_precipitation IS NULL OR d.om_precipitation::text='NaN' OR d.om_aerosol_optical_depth IS NULL OR d.om_aerosol_optical_depth::text='NaN') ORDER BY d.date DESC,d.station_id LIMIT 40",(lo,today));work=q.fetchall()
 c.commit()
 if not work:print('NO_MISSING_WEATHER',flush=True);break
 dt=work[0][1];work=[x for x in work if x[1]==dt];coords=list(dict.fromkeys((r[2],r[3]) for r in work));w=fetch('weather',coords,dt);a=fetch('aod',coords,dt);data={co:[*wv,av] for co,wv,av in zip(coords,w,a)}
 guard(c);ids=[x[0] for x in work]
 with c.cursor() as q:
  q.execute('SELECT station_id,date,parameter,'+','.join(fields)+' FROM daily_features WHERE station_id=ANY(%s) AND date=%s ORDER BY station_id,date,parameter FOR UPDATE',(ids,dt));before=q.fetchall()
  vals=[(r[0],str(dt),*data[(r[2],r[3])]) for r in work]
  sql='UPDATE daily_features d SET '+','.join(f+"=CASE WHEN d."+f+" IS NULL OR d."+f+"::text='NaN' THEN COALESCE(v."+f+'::double precision,d.'+f+') ELSE d.'+f+' END' for f in fields)+' FROM (VALUES %s) v(station_id,date,'+','.join(fields)+') WHERE d.station_id=v.station_id AND d.date=v.date::date'
  execute_values(q,sql,vals)
  q.execute('SELECT station_id,date,parameter,'+','.join(fields)+' FROM daily_features WHERE station_id=ANY(%s) AND date=%s ORDER BY station_id,date,parameter',(ids,dt));after=q.fetchall();assert len(before)==len(after)
  updated=0
  for old,new in zip(before,after):
   assert old[:3]==new[:3]
   for ov,nv in zip(old[3:],new[3:]):
    if not missing(ov):assert ov==nv,'finite cell changed'
    elif not missing(nv):assert math.isfinite(nv);updated+=1
 c.commit();repaired_rows+=len(after);record(dict(kind='daily_recent_recovery_verified',date=str(dt),stations=len(ids),rows=len(after),filled_cells=updated));print('REPAIRED',dt,len(ids),len(after),updated,flush=True);time.sleep(1)
from src.features import build_advanced_weather_features
if repaired_rows:
 guard(c);print('RECENT_DERIVED_RECOMPUTED',build_advanced_weather_features(c,force=True,since=str(lo)))
c.close()
if repaired_rows and os.environ.get('GITHUB_STEP_SUMMARY'):
 with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(f"Recent weather auto-repair: {repaired_rows} rows repaired; finite cells preserved.\n")
