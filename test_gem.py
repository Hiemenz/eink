import requests
import json
import yaml

def load_api_key(yaml_file="test_gem.yml"):
    """Load the API key from a YAML configuration file."""
    with open(yaml_file, "r") as file:
        config = yaml.safe_load(file)
    return config.get("GEMINI_API_KEY")

def generate_content(prompt, yaml_file="test_gem.yml"):
    """Send a request to the Gemini API with the given prompt."""
    api_key = load_api_key(yaml_file)
    if not api_key:
        raise ValueError("API key not found in YAML file.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": prompt}]}]}

    response = requests.post(url, headers=headers, data=json.dumps(data))

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Error: {response.status_code}, {response.text}")

# Example usage
if __name__ == "__main__":
    try:
        result = generate_content("strickly just give me the content and the tweets are divided by '|' do not label anything just provide the content. You are a world-class prompt engineer tasked with creating and solving challenging problems using innovative, creative prompts. you are a popular influencer that tweets engaging tweets that both illustrate your problem-solving techniques and share actionable tips for effective prompt engineering. Ensure your content is concise, insightful, and tailored to spark conversation on Twitter. Make the content engaging and viral to the forum in the voice of 20 something tech influencer. no hash tags in the tweets ")

        # Extract the text content
        text_content = result["candidates"][0]["content"]["parts"][0]["text"]

        print(text_content)
    except Exception as e:
        print(e)
  
  
# strickly just give me the content and the tweets are divided by '|' do not label anything just provide the content.  
# 
# You are a world-class prompt engineer tasked with creating and solving challenging problems using innovative, creative prompts.
# 
# you are a popular influencer that tweets engaging tweets that both illustrate your problem-solving techniques and share actionable tips for effective prompt engineering. Ensure your content is concise, insightful, and tailored to spark conversation on Twitter. Make the content engaging and viral to the forum in the voice of 20 something tech influencer. no hash tags in the tweets
  
  