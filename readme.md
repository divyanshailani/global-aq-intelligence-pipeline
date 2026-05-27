# 🇮🇳 India Air Quality — EDA & Prediction Pipeline

End-to-end data pipeline for Indian air quality analysis: **API ingestion → 5-phase cleaning → EDA → feature engineering → ML prediction.**

Built with real sensor data from [OpenAQ](https://openaq.org/) across 10 Indian monitoring stations (2015–2025).

## 📊 Key Findings

| Insight | Detail |
|---|---|
| Most polluted station | Income Tax Delhi (~225 µg/m³ avg PM2.5) |
| Cleanest station | Collectorate Gaya (~35 µg/m³) |
| Peak pollution hour | 8–10 AM (temperature inversion traps pollutants) |
| Cleanest hour | 3–5 PM (solar heating disperses pollution) |
| Station correlation | Delhi ↔ Mumbai = 0.97 (same weather systems) |
| Statistical anomalies | 331 readings beyond 3σ (>332 µg/m³) |

## 🏗️ Project Structure

```
pow-eda-pipeline/
├── data/
│   ├── raw/                    # Original API data (gitignored)
│   └── processed/              # Cleaned data (gitignored)
├── notebooks/
│   ├── 01_indian_aq_clean.ipynb    # 5-phase data cleaning
│   ├── 02_eda.ipynb                # Exploratory data analysis
│   └── 03_feature_engineering.ipynb # Feature engineering for ML
├── scripts/
│   └── fetch_openaq_india.py   # OpenAQ API data ingestion
├── src/
│   ├── aggregations.py         # Data aggregation utilities
│   └── process_aq.py           # Processing functions
├── tests/
│   └── test_processing.py      # Unit tests
├── .gitignore
└── readme.md
```

## 🧹 Cleaning Pipeline

```
Raw data:          105,265 rows × 8 columns
Phase 1 — NaN:      -1,100 rows (dropna on missing sensor readings)
Phase 2 — Placeholders: -5 rows (999.99 sentinel values)
Phase 3 — Negatives: -996 rows (-52°C stuck sensor in Gurugram)
Phase 4 — Outliers:  -692 rows (per-parameter domain thresholds)
Phase 5 — Dtypes:       0 rows (datetime conversion)
────────────────────────────────────────
Clean data:        102,472 rows (97.35% retained)
```

## 🛠️ Feature Engineering

| Feature Type | Features | Purpose |
|---|---|---|
| Time | month, day_of_week, is_weekend | Seasonal & weekly patterns |
| Lag | lag_1, lag_2, lag_3 | Temporal momentum (yesterday's PM2.5) |
| Rolling | roll_3_mean, roll_7_mean, roll_3_std | Short-term trend & volatility |

## 🔧 Tech Stack

- **Python 3.11+** — core language
- **Pandas** — data manipulation & time-series operations
- **Matplotlib / Seaborn** — visualization
- **NumPy** — numerical operations
- **scikit-learn** — ML models (upcoming)
- **OpenAQ API** — data source

## 🚀 Setup

```bash
# Clone
git clone https://github.com/divyanshailani/pow-eda-pipeline.git
cd pow-eda-pipeline

# Install dependencies
pip install pandas numpy matplotlib seaborn scikit-learn requests

# Run data ingestion
python scripts/fetch_openaq_india.py

# Open notebooks
jupyter notebook notebooks/
```

## 📈 Roadmap

- [x] Data ingestion (OpenAQ API)
- [x] 5-phase data cleaning
- [x] Exploratory data analysis
- [x] Feature engineering
- [ ] ML model (Linear Regression + Random Forest)
- [ ] PostgreSQL migration
- [ ] FastAPI prediction endpoint

## 👤 Author

**Divyansh Ailani** — [GitHub](https://github.com/divyanshailani)
