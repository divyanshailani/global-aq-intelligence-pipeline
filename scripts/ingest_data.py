
import requests
import os

def download_data(url, save_path):
    if not os.path.exists(save_path):
        print(f"Downloading data from {url}...")
        response = requests.get(url)
        with open(save_path, 'wb') as f:
            f.write(response.content)
            print("Download complete.")
    else:
        print(f"Data already exists at {save_path}.")