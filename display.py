import os
import json
import time
import sys
sys.path.append('lib')  # Ensure the library path is correct
from waveshare_epd import epd7in5_V2  # Adjust the import based on your specific model
from PIL import Image

starting_image = 0

def file_generator(base_path, current_count, start_image, increment, max_attempts=10):
    counter = current_count
    attempts = 0
    while attempts < max_attempts:
        filename = os.path.join(base_path, f"frame_{counter:04d}.bmp")
        print(filename)
        if not os.path.exists(filename):
            print('restarting reel... ')
            counter = start_image
            attempts += 1
            continue
        yield filename, counter
        counter += increment
        attempts = 0 
        
        
def load_json_file(file_name):
    data_dict = {
        'image_num': starting_image,
        'incriment_num': 1,
         'start_num' : 1,
         "movie_directory": "steam_boat_willie",
                 
                 }  # Start from 951 if file doesn't exist
    if not os.path.isfile(file_name):
        return data_dict
    with open(file_name, 'r') as file:
        data_dict = json.load(file)
    return data_dict

def save_dict_json_file(file_name, payload_dict):
    with open(file_name, 'w') as file:
        json.dump(payload_dict, file)
    
def display_single_image(image_file):
    # Initialize and clear the display
    epd = epd7in5_V2.EPD()
    epd.init()
    epd.Clear()

    # Load the processed image
    image = Image.open(f'{image_file}')

    # Display the image
    epd.display(epd.getbuffer(image))

    epd.sleep()
    print('display is sleeping...' )



def display_image_to_eink(directory):
    image_payload = load_json_file('image_payload.json')
    
    if image_payload['movie_directory'] != '':
        directory = image_payload['movie_directory']
    
    image_gen = file_generator(directory, image_payload['image_num'], image_payload['start_num'], image_payload['incriment_num'])
    
    try:
        file_path, counter = next(image_gen)  # Get the next image
        file_name = file_path.split('/')[-1].split('.')[0]
        print(f'Displaying... {directory}/{file_name}')
        display_single_image(f'{directory}/{file_name}')

        image_payload['image_num'] = image_payload['incriment_num'] + counter  # Update the image number
        save_dict_json_file('image_payload.json', image_payload)  # Save the updated number

    except StopIteration:
        print("No more images to display.")

#display_image_to_eink('fake fol  der')