import re

def clean_my_data(raw_string):
    # Target fields for the first and second categories
    target_categories = ['speedX', 'speedY', 'trackPos', 'angle', 'rpm', 'gear', 'wheelSpinVel',
    'damage', 'fuel', 'curLapTime', 'lastLapTime']

    cleaned_data = {}
    # Extract all (key value) pairs from the raw TORCS UDP packet
    all_pairs = re.findall(r'\(([^)\s]+)\s+([^)]+)\)', raw_string)

    # Phase 1: Filter fields and categorize data
    for key, value in all_pairs:
        if key in target_categories:
            if key == 'wheelSpinVel':
                # Split the 4 wheel speed values by space
                wheels = value.split()
                if len(wheels) == 4:
                    cleaned_data['wheel_FL'] = wheels[0] # Front Left
                    cleaned_data['wheel_FR'] = wheels[1] # Front Right
                    cleaned_data['wheel_RL'] = wheels[2] # Rear Left
                    cleaned_data['wheel_RR'] = wheels[3] # Rear Right
            else:
                cleaned_data[key] = value

    # Phase 2: Convert data types uniformly before returning
    for key, value in cleaned_data.items():
        if key in ['gear', 'damage']:
            cleaned_data[key] = int(value)
        else:
            cleaned_data[key] = float(value)

    return cleaned_data