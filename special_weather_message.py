import requests
import re

def get_special_weather_messages(url):
    response = requests.get(url)
    if response.status_code != 200:
        print(f"Failed to retrieve data. Status code: {response.status_code}")
        return None
    
    # Extract relevant weather messages using regex from <pre> tag
    matches = re.findall(r'<pre[^>]*>(.*?)</pre>', response.text, re.DOTALL)
    if matches:
        messages = '\n\n'.join(re.sub(r'<.*?>', '', match).strip() for match in matches)
        return messages
    else:
        return "No special weather messages found."

if __name__ == "__main__":
    url = "https://forecast.weather.gov/showsigwx.php?warnzone=TNZ027&warncounty=TNC037&firewxzone=TNZ027&local_place1=Nashville%20TN"
    messages = get_special_weather_messages(url)
    
    if messages:
        print("Special Weather Messages:")
        print(messages)


send text to LLM and return it 