from input import detect_input
from client import Client
from logger import CSVLogger

def main():
    handler = detect_input()
    handler.start()
    logger = CSVLogger()


    client = Client(handler, logger)
    client.start()


    handler.stop()
    logger.close()

if __name__ == '__main__':
    main()