import socket
import time

IP = '127.0.0.1'
PORT = 3001

client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
client.settimeout(10.0)

try:
    initmsg='SCR(init -45 -19 -12 -7 -4 -2.5 -1.7 -1 -.5 0 .5 1 1.7 2.5 4 7 12 19 45)'
    client.sendto(initmsg.encode(), (IP,PORT))
    print("Message sended.")
    while True:
        data, server = client.recvfrom(4096)
        print(data.decode())

        action_msg = "(accel 0.5)(brake 0.0)(steer 0.0)(gear 1)"
        client.sendto(action_msg.encode(), (IP, PORT))
        time.sleep(0.02)
except TimeoutError:
    print("TIME OUT")
finally:
    client.close()