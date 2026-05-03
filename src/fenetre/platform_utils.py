def is_raspberry_pi():
    try:
        with open('/sys/firmware/devicetree/base/model', 'r') as f:
            return "Raspberry Pi" in f.read()
    except FileNotFoundError:
        return False

        
    return False
