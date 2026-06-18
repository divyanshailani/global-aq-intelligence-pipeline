# 🌍 Global AQ Intelligence — Full-Stack Air Quality Prediction System

> **Executive Summary for Recruiters:** An end-to-end Machine Learning pipeline and full-stack architecture that ingests live OpenAQ data, fuses it with NASA satellite & Open-Meteo thermodynamics, trains Gradient Boosting Regressors, and exports mathematically rigorous 30-day PM2.5 forecasts to a high-performance Next.js site. 

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![Next.js](https://img.shields.io/badge/Next.js-15-black?style=flat&logo=next.js)](https://nextjs.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?style=flat&logo=postgresql&logoColor=white)](https://postgresql.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 🏛️ System Architecture

1. **Ingestion Engine:** Fetches live PM2.5 and co-pollutants across India, USA, UK, and Australia using the `OpenAQ API`.
2. **Data Fusion:** Integrates with `NASA POWER` and `Open-Meteo` for highly accurate, localized historical and future meteorology (temp, wind, precip, FIRMS fire detections).
3. **MLOps Training:** Generates over 31K feature vectors. Evaluated using a strict temporal holdout to prevent data leakage.
4. **V7 Thermodynamics Inference:** Direct multi-horizon forecasting (avoiding compounded chained errors) with a custom *Weather-Weighted Interpolator* for hyper-realistic forecast variance.
5. **Next.js Frontend:** A React-based UI mapping live predictions via server-sent events with seamless JSON-based static regeneration.

---

## 🗺️ Project Journey (7 Evolutionary Phases)

What started as a simple local EDA project evolved into a robust, global MLOps architecture over 7 major iterations:

- **Phase 1-3:** Data Cleaning & Context. Discovered that time-series models leak data without chronological splitting. Replaced Open-Meteo historicals with NASA POWER due to null-rate issues.
- **Phase 4:** Temporal Memory Breakthrough. Discovered that yesterday's PM2.5 (`lag_1`) carries 76% of the signal. Boosted R² from 0.31 to 0.97 by adding historical memory variables.
- **Phase 5:** Global Expansion. Scaled the architecture to support disparate environments (Indian Monsoons, Australian Bushfires, UK Maritime, USA Regulated AQI).
- **Phase 6:** The Chaining Problem. Our 30-day chained forecast compounded errors rapidly (day 2 fed into day 3, etc.).
- **Phase 7 (Current): V7 Direct Thermodynamics Engine:** 
  - Developed independent models for `1d`, `7d`, `14d`, and `30d` horizons. 
  - Eradicated exponential error compounding.
  - Deployed an Anchor Point strategy augmented with a physics-based Weather-Weighted Interpolator (applying mathematically modeled rain washouts and wind dispersion logic to the data).

---

## 📈 Performance Benchmarks

*Evaluated on a 20% temporal split (Global V7 Test Metrics):*

| Country | R² | MAE | Environment |
|---------|-----|-----|-------------|
| 🇺🇸 USA | 0.80 | 1.7 µg/m³ | Reference-grade sensors, stable baseline |
| 🇮🇳 India | 0.75 | 9.26 µg/m³ | High-variance seasonal spikes (Stubble burning/Monsoon) |
| 🇦🇺 Australia | 0.64 | 1.6 µg/m³ | Clean air, low variance |
| 🇬🇧 UK | 0.48 | 2.0 µg/m³ | Fragmented local sensors |

---

## 🚀 How to Run Locally

### 1. Database Setup
Ensure PostgreSQL is running locally (`brew services start postgresql@15`).
```bash
createdb indiaaq
```

### 2. Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the Admin Dashboard (Retrain & Deploy)
```bash
uvicorn scripts.admin_dashboard:app --reload
```
Navigate to `http://localhost:8000/admin`. From here, you can trigger data fetching, forecast generation, model retraining, and Vercel frontend deployments directly.

---

> **Note to Reviewers:** For detailed logs on how we overcame extreme Next.js caching barriers, temporal data leakage, and algorithmic forecasting errors, please refer to the [`ISSUES.md`](./ISSUES.md) document.
