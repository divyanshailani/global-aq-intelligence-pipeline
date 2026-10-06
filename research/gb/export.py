"""Read-only, station-checkpointed GB PM2.5 aggregation. No production writes."""
import json,os,time
from pathlib import Path
import psycopg2
import pandas as pd

OUT=Path(os.environ.get('GB_OUTPUT','/downloads/gb-retrain'));OUT.mkdir(parents=True,exist_ok=True)
def connect():
 c=psycopg2.connect(host=os.environ['POSTGRES_HOST'],user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],dbname=os.environ['POSTGRES_DB'],port=os.environ.get('POSTGRES_PORT','5432'),sslmode='require',connect_timeout=15,keepalives=1,keepalives_idle=20,keepalives_interval=10,keepalives_count=3,tcp_user_timeout=60000)
 c.set_session(readonly=True);return c

def main():
 c=connect()
 with c.cursor() as cur:
  cur.execute('SET statement_timeout=25000')
  cur.execute("SELECT s.id,s.latitude,s.longitude FROM stations s WHERE s.country_code='GB' ORDER BY s.id");stations=cur.fetchall()
  c.commit()
  parts=[]
  for off in range(0,len(stations),10):
   cur.execute("SELECT d.*,s.latitude,s.longitude FROM daily_features d JOIN stations s ON s.id=d.station_id WHERE d.station_id=ANY(%s) AND d.parameter='pm25' AND date < '2026-10-01' ORDER BY d.station_id,date",([r[0] for r in stations[off:off+10]],))
   parts.append(pd.DataFrame(cur.fetchall(),columns=[r[0] for r in cur.description]));c.commit()
  existing=pd.concat(parts,ignore_index=True)
 c.close();existing.to_parquet(OUT/'existing.parquet',index=False)
 # The original approved current-fleet cutoff, also used for archive import.
 fleet=existing[existing.date.astype(str)>='2026-07-01'].station_id.unique().tolist()
 (OUT/'fleet.json').write_text(json.dumps(fleet));print('FLEET',len(fleet),flush=True)
 stations=[r for r in stations if r[0] in fleet]
 for i,(sid,lat,lon) in enumerate(stations):
  p=OUT/f'raw_daily_{sid}.parquet'
  if p.exists():continue
  for attempt in range(3):
   c=None
   try:
    c=connect()
    with c.cursor() as cur:
     cur.execute('SET statement_timeout=45000')
     cur.execute("""SELECT (datetime_utc AT TIME ZONE 'Europe/London')::date AS date,
       unit,count(*) AS n_raw,
       count(*) FILTER (WHERE value>0 AND value<=500 AND value::text NOT IN ('NaN','Infinity','-Infinity')) AS n_valid,
       avg(value) FILTER (WHERE value>0 AND value<=500 AND value::text NOT IN ('NaN','Infinity','-Infinity')) AS value
       FROM raw_measurements WHERE station_id=%s AND parameter='pm25'
       AND datetime_utc >= '2015-01-01' AND datetime_utc < '2026-10-01'
       GROUP BY 1,2 ORDER BY 1,2""",(sid,))
     df=pd.DataFrame(cur.fetchall(),columns=['date','unit','n_raw','n_valid','value'])
    c.close();c=None
    df['station_id']=sid;df['latitude']=lat;df['longitude']=lon
    df.to_parquet(p,index=False);print('EXPORTED',i+1,len(stations),sid,len(df),flush=True);break
   except (psycopg2.OperationalError,psycopg2.InterfaceError):
    if c:c.close()
    if attempt==2:raise
    time.sleep([10,30,60][attempt])
 print('EXPORT_DONE',flush=True)

if __name__=='__main__':main()
