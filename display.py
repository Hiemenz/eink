import os
import json
import time
import sys
import fcntl
sys.path.append('lib')  # Ensure the library path is correct
from waveshare_epd import epd7in5_V2, epd7in3f  # Adjust the import based on your specific model
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


DISPLAY_LOCK_FILE = "/tmp/eink_display.lock"
_REFRESH_COUNTER_FILE = "/tmp/eink_refresh_count.txt"


def _read_refresh_count() -> int:
    try:
        with open(_REFRESH_COUNTER_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _write_refresh_count(n: int) -> None:
    try:
        with open(_REFRESH_COUNTER_FILE, "w") as f:
            f.write(str(n))
    except Exception:
        pass


def display_color_image(image_file, model='epd7in5_V2', full_clear_interval: int = 0) -> bool:
    """Display an image on the e-ink screen using the configured driver model.

    full_clear_interval: if > 0, run an extra deep-clear cycle every N refreshes
    to reduce ACeP 7-color ghosting. Has no effect on the B/W V2 driver.

    Never raises — hardware errors are caught and logged so a flaky panel can't
    crash the caller. Returns True if the image actually reached the panel,
    False otherwise (lock contention or a hardware error), so callers that
    track refresh health (e.g. main.py) can tell a real push from a no-op.
    """
    from waveshare_epd import epd7in5_V2, epd7in3f
    driver_map = {
        'epd7in5_V2': epd7in5_V2,
        'epd7in3f':   epd7in3f,
    }

    lock_fd = open(DISPLAY_LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        print("[display] Another display operation in progress, skipping.")
        lock_fd.close()
        return False

    epd = None
    try:
        driver = driver_map.get(model, epd7in5_V2)
        epd = driver.EPD()
        epd.init()

        # Periodic deep-clear for 7-color ACeP ghosting
        if full_clear_interval > 0 and model == 'epd7in3f':
            count = _read_refresh_count() + 1
            _write_refresh_count(count)
            if count % full_clear_interval == 0:
                print(f"[display] Deep clear #{count // full_clear_interval} (every {full_clear_interval} refreshes)")
                epd.Clear()   # white
                epd.Clear()   # second pass drives pigments fully to one extreme
                _write_refresh_count(0)

        epd.Clear()
        image = Image.open(image_file)
        epd.display(epd.getbuffer(image))
        epd.sleep()
        print(f'display is sleeping... (model: {model})')
        return True
    except Exception as e:
        print(f"[display] Error during display: {e}")
        if epd:
            try:
                epd.sleep()
            except Exception:
                pass
        return False
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()



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