import re

def clean_my_data(raw_string):
    # Target fields for the first and second categories
    target_categories = [
        'curLapTime', 'distFromStart', 'speedX', 'trackPos', 
        'angle', 'wheelSpinVel', 'throttle', 'brake', 
        'steer', 'gear', 'rpm', 'racePos', 'fuel'
    ]

    # Extract all (key value) pairs from the raw TORCS UDP packet
    all_pairs = re.findall(r'\(([^)\s]+)\s+([^)]+)\)', raw_string)

    # Dictionary to temporarily store raw data
    raw_data = {}
    for key, value in all_pairs:
        if key in target_categories:
            raw_data[key] = value

    # If critical fields are missing, return an empty dictionary to prevent errors down the line
    if not raw_data:
        return {}
    
    # Start data cleaning, unit conversion, and construct the final snake_case dictionary
    cleaned_data = {}

    # Timeline and lap time (converted from curLapTime)
    cur_lap_time = float(raw_data.get('curLapTime', 0.0))
    cleaned_data['timestamp'] = cur_lap_time
    cleaned_data['lap_time'] = cur_lap_time

    # Spatial axis
    cleaned_data['lap_distance'] = float(raw_data.get('distFromStart', 0.0))

    # Speed conversion (speedX * 3.6)
    speed_x = float(raw_data.get('speedX', 0.0))
    cleaned_data['speed_kmh'] = speed_x * 3.6

    # Basic vehicle dynamics
    cleaned_data['track_pos'] = float(raw_data.get('trackPos', 0.0))
    cleaned_data['angle'] = float(raw_data.get('angle', 0.0))

    # Wheel spin processing: Average the 4 wheel speeds into a single wheel_spin metric
    wheel_vel_str = raw_data.get('wheelSpinVel', '')
    wheels = wheel_vel_str.split()
    if len(wheels) == 4:
        wheel_avg = sum(float(w) for w in wheels) / 4.0
        cleaned_data['wheel_spin'] = wheel_avg
    else:
        cleaned_data['wheel_spin'] = 0.0

    # Driver inputs
    cleaned_data['throttle'] = float(raw_data.get('throttle', 0.0))
    cleaned_data['brake'] = float(raw_data.get('brake', 0.0))
    cleaned_data['steer'] = float(raw_data.get('steer', 0.0))

    # Engine and gear (converted to int and float according to specifications)
    cleaned_data['gear'] = int(raw_data.get('gear', 0))
    cleaned_data['rpm'] = float(raw_data.get('rpm', 0.0))

    # Context fields
    cleaned_data['race_pos'] = int(raw_data.get('racePos', 1))
    cleaned_data['fuel'] = float(raw_data.get('fuel', 0.0))

    return cleaned_data