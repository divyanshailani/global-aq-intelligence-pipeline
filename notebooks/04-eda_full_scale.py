
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import psycopg2

conn = psycopg2.connect(
    dbname = "indiaaq",
    user = "postgres",
    password = 8765,
    host = "localhost",
    port = 5432
)


sns.set_style("darkgrid")
plt.rcParams["figure.figsize"] = (12, 6)


# Top 20 Most Polluted Stations
query = """
SELECT s.name, ROUND(AVG(r.value)::numeric, 2) AS avg_pm25
FROM clean_measurements r 
JOIN stations s ON s.id = r.station_id
WHERE r.parameter = 'pm25'
GROUP BY s.name
ORDER BY avg_pm25 DESC
LIMIT 20;
"""

df_top = pd.read_sql(query, conn)

plt.figure(figsize=(12, 8))
plt.barh(df_top["name"], df_top["avg_pm25"], color="crimson")
plt.xlabel("Average PM2.5 (µg/m³)")
plt.title("Top 20 Most Polluted Stations in India (2021 - 2025) (PM2.5)")
plt.gca().invert_yaxis()
plt.tight_layout()
plt.show()

# Monthly PM2.5 Trends ( All India Average)
query_monthly = """
SELECT DATE_TRUNC('month', datetime_utc) as month,
         ROUND(AVG(value)::numeric, 2) AS avg_pm25
FROM clean_measurements
WHERE parameter = 'pm25'
GROUP BY 1
ORDER BY 1;
"""

df_monthly = pd.read_sql(query_monthly, conn)
plt.plot(df_monthly["month"], df_monthly["avg_pm25"], marker = "o", color = "orangered")
plt.xlabel("Month")
plt.ylabel("Average PM2.5 (µg/m³)")
plt.title("Monthly PM2.5 Trends in India (2021 - 2025)")
plt.xticks(rotation = 45)
plt.tight_layout()
plt.savefig("reports/monthly_pm25_trends.png", dpi = 150)
plt.show()

# parameter co-relation ( Daily values for a single station)
query_corr = """
SELECT DATE(datetime_utc) as date, parameter, AVG(value) as avg_value
FROM clean_measurements
WHERE station_id = 1
GROUP BY date, parameter;
"""

df_corr = pd.read_sql(query_corr, conn)
df_pivot = df_corr.pivot(index="date", columns="parameter", values="avg_value")
plt.figure(figsize=(10, 8))
sns.heatmap(df_pivot.corr(), annot=True, cmap="coolwarm", fmt=".2f")
plt.title("Correlation of Pollutants at Station 1 (2021 - 2025)")
plt.tight_layout()
plt.savefig("reports/pollutant_correlation.png", dpi = 150)
plt.show()