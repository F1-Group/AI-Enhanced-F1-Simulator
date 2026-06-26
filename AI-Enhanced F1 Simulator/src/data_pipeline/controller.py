class Controller():
    ACCEL_RISE_RATE = {
        -1: 0.3,
         1: 0.4,
         2: 0.6,
         3: 0.9,
         4: 1.5,
         5: 1.8,
         6: 2.5,
    }

    GEAR_SAFE_SPEED = {
        -1: 0,
         1: 0,
         2: 30,
         3: 60,
         4: 100,
         5: 150,
         6: 180,
    }

    ACCEL_FALL_RATE = 10.0
    BRAKE_RISE_RATE = 10.0
    BRAKE_FALL_RATE = 10.0
    LOW_SPEED  = 10.0 # m/s (36 km/h)
    HIGH_SPEED = 70.0 # m/s (250 km/h)
    STEER_RISE_HIGH = 0.2
    STEER_RISE_LOW = 3.0


    def __init__(self, handler):
        self.handler = handler


    def update_accel(self, delta, speed):
        gear = self.handler.state['gear']
        rise_rate = self.ACCEL_RISE_RATE.get(gear, 0.4)
        # if speed is too low for current gear, limit rise rate
        safe_speed = self.GEAR_SAFE_SPEED.get(gear, 0)
        if (speed < safe_speed):
            rise_rate = self.ACCEL_RISE_RATE[1]

        if (self.handler.accel_pressed):
            self.handler.state['accel'] = min(1.0, self.handler.state['accel'] + rise_rate * delta)
        else:
            self.handler.state['accel'] = max(0.0, self.handler.state['accel'] - self.ACCEL_FALL_RATE * delta)
    
    def update_brake(self, delta):
        if self.handler.brake_pressed:
            self.handler.state['brake'] = min(1.0, self.handler.state['brake'] + self.BRAKE_RISE_RATE * delta)
        else:
            self.handler.state['brake'] = max(0.0, self.handler.state['brake'] - self.BRAKE_FALL_RATE * delta)

    def _get_steer_rate(self, speed):
        # Linear interpolation between low and high speed
        t = (speed - self.LOW_SPEED) / (self.HIGH_SPEED - self.LOW_SPEED)
        t = max(0.0, min(1.0, t))
        return self.STEER_RISE_LOW + t * (self.STEER_RISE_HIGH - self.STEER_RISE_LOW)
    
    def update_steer(self, delta, speed):
        rate = self._get_steer_rate(speed)
        is_turning = self.handler.steer_left_pressed or self.handler.steer_right_pressed

        # Double the steering rate when centering to improve responsiveness.
        if not is_turning:
            rate *= 2.0

        target = 0.0
        if self.handler.steer_left_pressed:
            target = 1.0
        elif self.handler.steer_right_pressed:
            target = -1.0

        if self.handler.state['steer'] < target:
            self.handler.state['steer'] = min(target, self.handler.state['steer'] + rate * delta)
        elif self.handler.state['steer'] > target:
            self.handler.state['steer'] = max(target, self.handler.state['steer'] - rate * delta)



    
