#!/usr/bin/env python3
"""Generate a QR code for your WiFi network to let others quickly connect.
"""

import getpass

import qrcode
import qrcode.image.pil

AUTHENTICATION_TYPES = ['WPA', 'WEP', 'nopass']


def wifi_code(ssid: str, hidden: str, authentication_type: str, password: str | None = None) -> str:
    # TODO: Check docstring format
    # TODO: Check Pypi installation
    # TODO: Flag to display qr code
    # TODO: Flag to check version
    """Generate a WiFi code for the given parameters

    :ssid str: SSID
    :hidden bool: Specify if the network is hidden
    :authentication_type str: Specify the authentication type. Supported types: WPA, WEP, nopass
    :password Optional[str]: Password. Not required if authentication type is nopass

    :return: The WiFi code for the given parameters
    :rtype: str
    """
    hidden = 'true' if hidden else 'false'

    if authentication_type in ['WPA', 'WEP']:
        if password is None:
            raise TypeError('For WPA and WEP, password should not be None.')
        return f'WIFI:T:{authentication_type};S:"{ssid}";P:"{password}";H:{hidden};;'
    if authentication_type == 'nopass':
        if password is not None:
            raise TypeError('For nopass, password should be None.')
        return f'WIFI:T:nopass;S:"{ssid}";H:{hidden};;'
    raise ValueError(f'Unknown authentication_type: {authentication_type}')


def wifi_qrcode(ssid: str, hidden: bool, authentication_type: str, password: str | None = None,
                **kwargs) -> qrcode.image.pil.Image:
    """Generate WiFi QR code for given parameters

    :ssid str: SSID
    :hidden bool: Specify if the network is hidden
    :authentication_type str: Specify the authentication type. Supported types: WPA, WEP, nopass
    :password Optional[str]: Password. Not required if authentication type is nopass
    :kwargs: Optional keyword arguments to use with `qrcode.make`. See the arguments for 
    `qrcode.QRCode`.

    :return: An image of the qrcode for the given parameters
    :rtype: qrcode.image.pil.Image
    """

    return qrcode.make(wifi_code(ssid, hidden, authentication_type, password), **kwargs).get_image()


def main():
    ssid = input('SSID: ')
    if ssid == '':
        print('Input is not valid!')
        return

    hidden_input = input('Is the network hidden [y/N]: ').lower()
    if hidden_input in ['yes', 'y', 'true', 't']:
        hidden = True
    elif hidden_input in ['', 'no', 'n', 'false', 'f']:
        hidden = False
    else:
        print('Input is not valid!')
        return

    print('Authentication types:')
    for i, authentication_type in enumerate(AUTHENTICATION_TYPES):
        print(f'- [{i+1}] {authentication_type}')
    authentication_type_i = input(
        f'Select authentication type [1-{len(AUTHENTICATION_TYPES)}]: ')
    try:
        authentication_type = AUTHENTICATION_TYPES[int(
            authentication_type_i)-1]
    except (ValueError, IndexError):
        print('Input is not valid!')
        return

    if authentication_type != 'nopass':
        password = getpass.getpass("Password: ")
        if password == "":
            print("Input not valid!")
    qrcode_img = wifi_qrcode(ssid, hidden, authentication_type, password)
    qrcode_img.save(ssid+'.png')
    print("The QR code has been stored in the current directory.")


if __name__ == '__main__':
    main()
