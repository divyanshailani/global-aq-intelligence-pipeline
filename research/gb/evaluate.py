"""Private calendar-aligned features and purged evaluation, never deployment."""
import argparse,json,os,time
from pathlib import Path
import numpy as np
import pandas as pd
import xgboost as xgb

F=['month','day_of_week','is_weekend','day_of_year','lag_1','lag_2','lag_3','lag_7','lag_14','lag_21','lag_30','roll_3_mean','roll_7_mean','roll_3_std','roll_14_mean','roll_30_mean','roll_14_std','om_temperature','om_wind_speed','om_precipitation','om_aerosol_optical_depth','rolling_3day_precip','aod_volatility_index','latitude','longitude']
H=[1,7,14,30]
P=dict(n_estimators=2000,learning_rate=.05,max_depth=6,subsample=.8,colsample_bytree=.8,min_child_weight=5,reg_lambda=5,n_jobs=2,tree_method='hist',early_stopping_rounds=50,random_state=0)
WINDOWS=[('2025-10-01','2025-12-31'),('2026-01-01','2026-03-31'),('2026-04-01','2026-05-31'),('2026-06-01','2026-07-31'),('2026-08-01','2026-09-30')]
OUT=Path(os.environ.get('GB_OUTPUT','/downloads/gb-retrain'))
REPO=Path(__file__).resolve().parents[2]

def build():
 existing=pd.read_parquet(OUT/'existing.parquet');existing['date']=pd.to_datetime(existing.date)
 raw=pd.concat([pd.read_parquet(p) for p in sorted(OUT.glob('raw_daily_*.parquet'))],ignore_index=True)
 raw['date']=pd.to_datetime(raw.date)
 units=raw.groupby('unit',dropna=False).n_raw.sum().to_dict()
 # Concentrations must be micrograms/m3, not silently mix ppb or mg/m3.
 valid_units={'µg/m³','μg/m³','ug/m3','µg/m3','ug/m³'}
 unknown=raw[~raw.unit.isin(valid_units)]
 if len(unknown):raise ValueError(f'Unsupported PM2.5 units: {unknown.unit.unique().tolist()}')
 raw=raw[raw.n_valid>0].copy();raw['weighted']=raw.value*raw.n_valid
 raw=raw.groupby(['station_id','date'],as_index=False).agg(weighted=('weighted','sum'),n_valid=('n_valid','sum'),latitude=('latitude','first'),longitude=('longitude','first'))
 raw['value']=raw.weighted/raw.n_valid
 old=existing[['station_id','date','value','latitude','longitude']].copy()
 # Preserve existing daily targets; only raw-derived station-days absent there are added.
 combined=pd.concat([old,raw[['station_id','date','value','latitude','longitude']]],ignore_index=True).drop_duplicates(['station_id','date'],keep='first')
 combined=combined[np.isfinite(combined.value)&(combined.value>0)&(combined.value<=500)]
 weather=['om_temperature','om_wind_speed','om_precipitation','om_aerosol_optical_depth','rolling_3day_precip','aod_volatility_index']
 parts=[]
 for sid,g in combined.groupby('station_id'):
  g=g.set_index('date').sort_index().asfreq('D')
  g['station_id']=sid;g['latitude']=g.latitude.ffill().bfill();g['longitude']=g.longitude.ffill().bfill()
  g['month']=g.index.month;g['day_of_week']=g.index.dayofweek;g['is_weekend']=(g.day_of_week>=5).astype(int);g['day_of_year']=g.index.dayofyear
  for lag in [1,2,3,7,14,21,30]:g[f'lag_{lag}']=g.value.shift(lag)
  prior=g.value.shift(1)
  for w in [3,7,14,30]:g[f'roll_{w}_mean']=prior.rolling(w,min_periods=w).mean()
  for w in [3,14]:g[f'roll_{w}_std']=prior.rolling(w,min_periods=w).std()
  g=g.reset_index().merge(existing[existing.station_id==sid][['date']+weather],on='date',how='left',validate='one_to_one')
  # Historical covariates absent remain NULL; no external enrichment or zeros.
  parts.append(g[['station_id','date','value']+F].dropna(subset=['value']))
 d=pd.concat(parts,ignore_index=True).sort_values(['station_id','date']).reset_index(drop=True)
 for col in F+['value']:d[col]=pd.to_numeric(d[col],errors='coerce').replace([np.inf,-np.inf],np.nan).astype('float32')
 d.to_parquet(OUT/'train_daily.parquet',index=False)
 report={'rows':len(d),'stations':int(d.station_id.nunique()),'dates':[str(d.date.min()),str(d.date.max())],'units':units,'by_year':{str(k):int(v) for k,v in d.groupby(d.date.dt.year).size().items()},'weather_nonnull_by_year':{str(y):float(g.om_temperature.notna().mean()) for y,g in d.groupby(d.date.dt.year)},'raw_valid_observations':int(raw.n_valid.sum()),'calendar_lags':True,'existing_targets_preferred':True}
 (OUT/'data_report.json').write_text(json.dumps(report,indent=2));print('DATA',json.dumps(report),flush=True);return d

def targets(d,h):
 y=d[['station_id','date','value']].rename(columns={'value':'target'});y['date']-=pd.Timedelta(days=h)
 return d.merge(y,on=['station_id','date'],how='inner',validate='one_to_one')

def fit(q,h,cutoff):
 trall=q[q.date<cutoff-pd.Timedelta(days=h+1)]
 vc=trall.date.max()-pd.Timedelta(days=60)
 va=trall[trall.date>vc];tr=trall[trall.date<=vc-pd.Timedelta(days=h+1)]
 if len(tr)<500 or len(va)<50:raise ValueError('insufficient split')
 m=xgb.XGBRegressor(**P).fit(tr[F],tr.target,eval_set=[(va[F],va.target)],verbose=False)
 return m,len(tr),len(va)

def main():
 d=build();existing=pd.read_parquet(OUT/'existing.parquet');existing['date']=pd.to_datetime(existing.date)
 archive_free=d.merge(existing[['station_id','date']],on=['station_id','date'],how='inner',validate='one_to_one')
 fleet=set(json.loads((OUT/'fleet.json').read_text()));results=[];models_by_window={a:{} for a,b in WINDOWS}
 for h in H:
  q=targets(d,h);q_old=targets(archive_free,h);champ=xgb.XGBRegressor();champ.load_model(REPO/f'models/v12/GB/horizon_{h}/model.json')
  for a,b in WINDOWS:
   dest=OUT/f'eval_h{h}_{a}.json'
   a2=pd.Timestamp(a);te=q[(q.date>=a2)&(q.date<=pd.Timestamp(b))&q.station_id.isin(fleet)]
   if len(te)<100:continue
   m,nt,nv=fit(q,h,a2);models_by_window[a][h]=m
   actual=te.target.to_numpy();pred=m.predict(te[F]);old=champ.predict(te[F]);pers=te.value.to_numpy();roll=te.roll_7_mean.to_numpy()
   mae=lambda x:float(np.nanmean(np.abs(actual-x)))
   r={'h':h,'window':[a,b],'train_rows':nt,'validation_rows':nv,'test_rows':len(te),'test_stations':int(te.station_id.nunique()),'best_iteration':int(m.best_iteration),'new_mae':mae(pred),'champ_mae':mae(old),'persistence_mae':mae(pers),'roll7_mae':mae(roll),'roll7_n':int(np.isfinite(roll).sum()),'mase':mae(pred)/mae(pers),'mean_actual':float(actual.mean()),'champ_fair':a>='2026-06-01'}
   if a=='2026-08-01':
    ablation,_,_=fit(q_old,h,a2);r['without_archive_calendar_mae']=mae(ablation.predict(te[F]))
   dest.write_text(json.dumps(r,indent=1));results.append(r);print('EVAL',json.dumps(r),flush=True)
   p=OUT/f'window_{a}_h{h}.json';m.save_model(p)
  # Final candidates use all eligible history, with last 60 days for early stop.
  selected,ntr,nva=fit(q,h,q.date.max()+pd.Timedelta(days=h+2))
  params=dict(P);params.pop('early_stopping_rounds');params['n_estimators']=int(selected.best_iteration)+1
  final=xgb.XGBRegressor(**params).fit(q[F],q.target,verbose=False);final.save_model(OUT/f'candidate_h{h}.json')
  print('FINAL',h,ntr,nva,int(selected.best_iteration),flush=True)
 (OUT/'eval.json').write_text(json.dumps(results,indent=2))
 champions={}
 for h in H:
  c=xgb.XGBRegressor();c.load_model(REPO/f'models/v12/GB/horizon_{h}/model.json');champions[h]=c
 daily=d[d.station_id.isin(fleet)].groupby('date').value.mean();agg=[]
 for a,b in WINDOWS:
  mods=models_by_window[a]
  if len(mods)!=4:continue
  # Same live interpolation path. All valid origins within each test window.
  errors={'new':[],'champ':[],'persistence':[]};last=pd.Timestamp(b)
  for origin in pd.date_range(a,b):
   x=d[(d.date==origin)&d.station_id.isin(fleet)]
   if len(x)<20:continue
   anchors={h:float(mods[h].predict(x[F]).mean()) for h in H}
   old={}
   for h in H:
    old[h]=float(champions[h].predict(x[F]).mean())
   for name,v in [('new',anchors),('champ',old)]:
    path=np.maximum(np.interp(np.arange(1,31),H,[v[h] for h in H]),0)
    for day,pred in enumerate(path,1):
     target=origin+pd.Timedelta(days=day)
     if target in daily.index and target<=last:errors[name].append(abs(pred-daily[target]))
   for day in range(1,31):
    target=origin+pd.Timedelta(days=day)
    if target in daily.index and target<=last:errors['persistence'].append(abs(daily[origin]-daily[target]))
  r={'window':[a,b],**{k:{'mae':float(np.mean(v)),'n':len(v)} for k,v in errors.items()}};agg.append(r);print('AGG',json.dumps(r),flush=True)
 (OUT/'aggregate_eval.json').write_text(json.dumps(agg,indent=2));print('DONE',flush=True)

if __name__=='__main__':main()
