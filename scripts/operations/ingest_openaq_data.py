import os
import requests

key = os.environ["OPENAQ_API_KEY"]

response = requests.get(
    "https://api.openaq.org/v3/countries/101",  # 101 = India
    headers={"X-API-Key": key}
)

print(response.status_code)  # Should print 200
print(response.json())       # India's info