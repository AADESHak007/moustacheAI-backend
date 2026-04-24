import requests
import os

# --- Configuration ---
API_URL = "http://localhost:8000/api/generate"
INPUT_IMAGE = "test_input.png"
OUTPUT_IMAGE = "test_output.jpg"
STYLE = "chevron"  # Try: chevron, pencil, walrus, k_style, etc.

def test_generation():
    if not os.path.exists(INPUT_IMAGE):
        print(f"Error: {INPUT_IMAGE} not found. Please save your image as {INPUT_IMAGE} in this folder.")
        return

    print(f"Sending {INPUT_IMAGE} to Gemini (Style: {STYLE})...")
    
    with open(INPUT_IMAGE, "rb") as f:
        files = {"image": f}
        data = {"style_id": STYLE}
        
        try:
            response = requests.post(API_URL, files=files, data=data)
            
            if response.status_code == 200:
                with open(OUTPUT_IMAGE, "wb") as out:
                    out.write(response.content)
                print(f"Success! Generated image saved as: {OUTPUT_IMAGE}")
            else:
                print(f"Failed: {response.status_code}")
                try:
                    print(response.json())
                except:
                    print(response.text)
        except Exception as e:
            print(f"Connection Error: {e}. Is the server running?")

if __name__ == "__main__":
    test_generation()
