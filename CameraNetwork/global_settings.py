#
# Copyright (C) 2017, Amit Aides, all rights reserved.
#
# This file is part of Camera Network
# (see https://bitbucket.org/amitibo/cameranetwork_git).
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
# 1)  The software is provided under the terms of this license strictly for
#     academic, non-commercial, not-for-profit purposes.
# 2)  Redistributions of source code must retain the above copyright notice, this
#     list of conditions (license) and the following disclaimer.
# 3)  Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions (license) and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
# 4)  The name of the author may not be used to endorse or promote products derived
#     from this software without specific prior written permission.
# 5)  As this software depends on other libraries, the user must adhere to and keep
#     in place any licensing terms of those libraries.
# 6)  Any publications arising from the use of this software, including but not
#     limited to academic journal and conference publications, technical reports and
#     manuals, must cite the following works:
#     Dmitry Veikherman, Amit Aides, Yoav Y. Schechner and Aviad Levis,
#     "Clouds in The Cloud" Proc. ACCV, pp. 659-674 (2014).
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR "AS IS" AND ANY EXPRESS OR IMPLIED
# WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO
# EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Globals
"""

from __future__ import division
import os
import warnings


##############################################################
# Global settings
##############################################################

#
# General
#
MIN_JPEG_QUALITY = 70

#
# Internet watchdog
#
WD_TEST_INTERNET_PERIOD = 120
WD_INTERNET_FAILURE_THRESH = 0

DEFAULT_PROXY_PARAMS = """
{
    "autossh_monitor_port": 10000,
    "ip": "3.123.49.101",
    "proxy_port": 1980,
    "client_port": 1981,
    "hb_port": 1985,
    "tunnel_port": 20000,
    "user": "ubuntu"
}
"""

#
# Configuration server
#
CONFIGURATION_SERVER = 'tx.technion.ac.il'
CONFIGURATION_SERVER_USER = 'amitibo' # TODO Update?
CONFIGURATION_SERVER_URL_BASE = 'http://%s/~amitibo/cameras_settings/' % CONFIGURATION_SERVER # TODO Update?
CONFIGURATION_SERVER_BASE_PATH = 'public_html/cameras_settings'

PROXY_SETTINGS_FILE_NAME = 'proxy_server_{timestamp}.json'
PROXY_IP = 'ip'
PROXY_USER = 'user'
PROXY_PORT = 'proxy_port'
CLIENT_PORT = 'client_port'
HB_PORT = 'hb_port'
TUNNEL_PORT = 'tunnel_port'
MONITOR_PORT = 'autossh_monitor_port'
PROXY_SETTINGS_DICT = {
    #
    # ip : ip of the proxy server
    #
    PROXY_IP: '',
    #
    # user : user of the proxy server
    #
    PROXY_USER: 'ubuntu',
    #
    # proxy_port : input port of the proxy
    # note
    # ----
    # All proxy ports should be higher then 1024 or else the zmq can't bind to them.
    #
    PROXY_PORT: 5550,
    CLIENT_PORT: 5560,
    HB_PORT: 5570,

    #
    # tunnel_port : Base of tunnels' remote side port numbers.
    #
    TUNNEL_PORT: 20000,
    #
    # autossh_monitor_port: Base of tunnel's monitor port number.
    #
    MONITOR_PORT: 10000
}

#
# How many seconds to wait before checking the state of the ssh tunnel command.
#
SSH_TUNNEL_WAIT_TIME = 2

#
# Identities of the proxy sockets used for routing the messages.
#
PROXY_DEALER_IDENTITY = 'PROXY_DEALER'
PROXY_ROUTER_IDENTITY = 'PROXY_ROUTER'

#
# Server parameters
#
PI_SERVER = 'pi'
ODROID_SERVER = 'odroid'
PI_USER = 'pi'
PI_PW = 'raspberry'
ODROID_USER = 'odroid'
ODROID_PW = 'odroid'

IDENTITY_FILE = os.path.join(os.path.expanduser('~'), 'AdiKP2.pem') #  changed from 'cameranetwork.pem' - Adi

#
# Dropbox folder
#
DROPBOX_CALIB_PATH = 'calib'
DROPBOX_LOOP_PATH = 'loop'
UPLOAD_PATH = '"SKY_CAMS_UPLOADS/{operation}/{camera_identity}/{subfolder}/{filename}"'

#
# Commands
# Note: In the reverse ssh command I added the exec so that it will be possible the kill the process.
# without the exec, the shell=True option causes the shell to open a separate process with a different
# pid.
#
REVERSE_AUTOSSH_CMD = 'AUTOSSH_DEBUG=1 exec autossh -M 0 -v -i {identity_file} -o "ExitOnForwardFailure yes" -o' \
                      ' "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -N -R' \
                      ' {tunnel_port}:localhost:{local_port} {server_user}@{server_ip}'

REVERSE_SSH_CMD = "exec ssh -i {identity_file} -oExitOnForwardFailure=yes -oServerAliveInterval=60 -N -R" \
                  " {tunnel_port}:localhost:{local_port} {server_user}@{server_ip}"

SCP_CMD = "scp -i {identity_file} {src_path} {server_user}@{server_ip}:{dst_path}"

TUNNEL_DESCRIPTION = "{tunnel_port}:localhost:{local_port} {server_user}@{server_ip}"

#
# Camera settings and commands
#
CAMERA_IDENTITY = 'id'
CELL_COMPANY = 'cell_company'
START_LOOP = 'start_loop'
LOOP_DELAY = 'loop_delay'
CALIBRATION_DELAY = 'calib_delay'
DAY_SETTINGS = 'day'
NIGHT_SETTINGS = 'night'
IMAGE_AWB = 'awb'
IMAGE_EXPOSURE = 'exposure_us'
IMAGE_GAIN = 'gain_db'
FRAMES_NUM = 'frames_num'
DAY_PERIOD_START = 'day_start'
DAY_PERIOD_END = 'day_end'
COLOR_MODE = 'color_mode'
HDR_MODE = 'hdr'
GAIN_BOOST = 'gain_boost'
COLOR_RAW = 'raw'
COLOR_RGB = 'rgb'
UPLOAD_JPG_FILE = 'upload_jpg'
UPLOAD_MAT_FILE = 'upload_mat'
INTERNET_FAILURE_THRESH = 'internet_failure_thresh'
SUNSHADER_MIN_ANGLE = 'sunshader_min'
SUNSHADER_MAX_ANGLE = 'sunshader_max'
CAMERA_LONGITUDE = 'longitude'
CAMERA_LATITUDE = 'latitude'
CAMERA_ALTITUDE = 'altitude'
MAX_CAMERA_RETRIES = 3
CAMERA_RESTART_PERIOD = 4

DEFAULT_LONGITUDE = 35.024963
DEFAULT_LATITUDE = 32.775776
DEFAULT_ALTITUDE = 229

DEFAULT_GRID_LONGITUDE = 35.024963
DEFAULT_GRID_LATITUDE = 32.775776
DEFAULT_GRID_ALTITUDE = 0

CAMERA_SETTINGS = {
    CAMERA_IDENTITY: '',
    SUNSHADER_MIN_ANGLE: 0,
    SUNSHADER_MAX_ANGLE: 180,
    CAMERA_LATITUDE: DEFAULT_LATITUDE,
    CAMERA_LONGITUDE: DEFAULT_LONGITUDE,
    CAMERA_ALTITUDE: DEFAULT_ALTITUDE,
    INTERNET_FAILURE_THRESH: WD_INTERNET_FAILURE_THRESH,
}

CAPTURE_SETTINGS = {
    START_LOOP: True,
    DAY_PERIOD_START: 9,
    DAY_PERIOD_END: 19,
    UPLOAD_JPG_FILE: False,
    UPLOAD_MAT_FILE: False,
    DAY_SETTINGS: {
        LOOP_DELAY: 300,
        IMAGE_EXPOSURE: 50,
        IMAGE_GAIN: 0,
        GAIN_BOOST: False,
        FRAMES_NUM: 5,
        COLOR_MODE: COLOR_RAW,
        HDR_MODE: 4
        },
    NIGHT_SETTINGS: {
        LOOP_DELAY: 1800,
        IMAGE_EXPOSURE: 8000000,
        IMAGE_GAIN: 0,
        GAIN_BOOST: True,
        FRAMES_NUM: 1,
        COLOR_MODE: COLOR_RAW,
        HDR_MODE: 1
        },
}

#
# Camera state
#
HALT_STATE = 0
LOOP_STATE = 1
CALIB_STATE = 2

#
# Calibration state
#
CALIBRATION_OK = 'OK'
CALIBRATION_MISSING = 'Missing'
CALIBRATION_FAILED = 'Failed'
CALIBRATION_RUNNING = 'Running'

EXTRINSIC_CALIBRATION_MIN_PTS = 30
EXTRINSIC_SETTINGS_FILENAME = "extrinsic_data.npy"
RADIOMETRIC_SETTINGS_FILENAME = "radiometric.pkl"
VIGNETTING_SETTINGS_FILENAME = "vignetting.pkl"
INTRINSIC_SETTINGS_FILENAME = "fisheye.pkl"

DEFAULT_NORMALIZATION_SIZE = 501
#
# Amit:
# The default radiometric settings were taken from camera 109.
#
DEFAULT_RADIOMETRIC_SETTINGS = \
    [0.077523644728250449, 0.11163146919636868, 0.11847859109577621]

#
# Dropbox
#

#
# Msg constants
#
MSG_TYPE_FIELD = 'cmd'

MSG_TYPE_ARRAY = 'array'
MSG_TYPE_CALIBRATION = 'calibration'
MSG_TYPE_CALIBRATION_STATUS = 'calibration_status'
MSG_TYPE_DARK_IMAGES = 'dark_images'
MSG_TYPE_DAYS = 'days'
MSG_TYPE_SUN_DAYS = 'sun_days'
MSG_TYPE_GET_SETTINGS = 'get_settings'
MSG_TYPE_HALT = 'halt'
MSG_TYPE_INIT = 'init'
MSG_TYPE_LOOP = 'loop'
MSG_TYPE_MOON = 'moon'
MSG_TYPE_PREVIEW = 'preview'
MSG_TYPE_RADIOMETRIC ='radiometric'
MSG_TYPE_REBOOT ='reboot'
MSG_TYPE_RESET_CAMERA = 'reset_camera'
MSG_TYPE_RESTART = 'restart'
MSG_TYPE_QUERY = 'query'
MSG_TYPE_SEEK = 'seek'
MSG_TYPE_SET_SETTINGS = 'set_settings'
MSG_TYPE_STATUS = 'status'
MSG_TYPE_EXTRINSIC = 'extrinsic'
MSG_TYPE_SAVE_EXTRINSIC = 'save_extrinsic'
MSG_TYPE_SPRINKLER = 'sprinkler'
MSG_TYPE_SUNSHADER = 'sunshader'
MSG_TYPE_SUNSHADER_SCAN = 'sunshader_scan'
MSG_TYPE_THUMBNAILS = 'thumbnails'
MSG_TYPE_TUNNEL = 'tunnel'
MSG_TYPE_TUNNEL_CHECK = 'tunnel_details'
MSG_TYPE_LOCAL = 'local_ip'
MSG_TYPE_UPDATE = 'update'

MSG_STATUS_FIELD = 'status'
MSG_STATUS_OK = 'ok'
MSG_STATUS_ERROR = 'error'
MSG_STATUS_WARNING = 'warning'

MSG_EXCEPTION_MAP = {
    MSG_STATUS_ERROR: Exception,
    MSG_STATUS_WARNING: warnings.warn
}

MSG_DATA_FIELD = 'msg'

MSG_TUNNEL_STATE = 'state'
MSG_TUNNEL_PORT = 'port'
MSG_TUNNEL_IP = 'ip'
MSG_TUNNEL_USER = 'user'
MSG_TUNNEL_PASS = 'pass'

MSG_IMG_BYTES = 'img_bytes'

#
# Messages that can be broadcast to local servers
#
LOCAL_MESSAGES = (MSG_TYPE_DAYS, MSG_TYPE_SEEK, MSG_TYPE_QUERY, MSG_TYPE_THUMBNAILS)

#
# GUI constants.
#
GUI_STARTUP_DELAY = 0

#
# Sunshader parameters
#
SUNSHADER_PERIOD = 60
SUNSHADER_SCAN_PERIOD = 180
SUNSHADER_SCAN_PERIOD_LONG = 360
SUNSHADER_MIN_ANGLES = 100
SUNSHADER_SCAN_DELTA_ANGLE = 20
SUNSHADER_MIN_MEASURED = 20
SUNSHADER_MAX_MEASURED = 160

#
# Sprinkler parameters
#
SPRINKLER_PERIOD = 0.5
SPRINKLER_LAST_DAY_FILE = os.path.expanduser('~/.sprinkler_last_day.pkl')
SPRINKLER_TIMER_HOUR = 7

#
# Sun Mask parameters
#
MASK_PERIOD = 300

#
# Server-Controller commands.
#
ARRAY_CMD = 'array'
CALIBRATION_CMD = 'calibration'
DARK_IMAGES_CMD = 'dark_images'
LOOP_CMD = 'loop'
MASK_CALC_CMD = 'mask_calc'
MOON_CMD = 'moon'
QUERY_CMD = 'query'
RADIOMETRIC_CMD = 'radiometric'
RESET_CAMERA_CMD = 'reset_camera'
SEEK_CMD = 'seek'
EXTRINSIC_CMD = 'extrinsic'
SAVE_EXTRINSIC_CMD = 'save_extrinsic'
RESTART_CMD = 'restart'
SPRINKLER_CMD = 'sprinkler'
SUNSHADER_CMD = 'sunshader'
SUNSHADER_UPDATE_CMD = 'sunshader_update'
SUNSHADER_SCAN_CMD = 'sunshader_scan'

#
# Day/Night parameters
#
SUN_ALTITUDE_DAY_THRESH = -0.1
SUN_ALTITUDE_SUNSHADER_THRESH = 0
SUN_ALTITUDE_EXPOSURE_THRESH = 0.001


#
# Setup paths
#
def initPaths(HOME_PATH=None):
    """Delayed settings of paths. This allows settings path locally (on pc) or camera."""

    if HOME_PATH is None:
        HOME_PATH = os.path.expanduser('~')

    global CAPTURE_PATH
    global GENERAL_SETTINGS_PATH
    global CAPTURE_SETTINGS_PATH
    global DEFAULT_LOG_FOLDER
    global MASK_PATH
    global INTRINSIC_SETTINGS_PATH
    global EXTRINSIC_SETTINGS_PATH
    global SUN_POSITIONS_PATH
    global DARK_IMAGES_PATH
    global UPLOAD_CMD
    global VIGNETTING_SETTINGS_PATH
    global RADIOMETRIC_SETTINGS_PATH

    CAPTURE_PATH = os.path.join(HOME_PATH, 'captured_images')
    GENERAL_SETTINGS_PATH = os.path.join(HOME_PATH, '.camera_data.json')
    CAPTURE_SETTINGS_PATH = os.path.join(HOME_PATH, '.capture_data.json')
    VIGNETTING_SETTINGS_PATH = os.path.join(HOME_PATH, VIGNETTING_SETTINGS_FILENAME)
    RADIOMETRIC_SETTINGS_PATH = os.path.join(HOME_PATH, RADIOMETRIC_SETTINGS_FILENAME)
    DEFAULT_LOG_FOLDER = os.path.join(HOME_PATH, 'camera_logs')
    MASK_PATH = os.path.join(HOME_PATH, 'mask_img.mat')

    #
    # Calibration parameters
    #
    INTRINSIC_SETTINGS_PATH = os.path.join(HOME_PATH, INTRINSIC_SETTINGS_FILENAME)
    EXTRINSIC_SETTINGS_PATH = os.path.join(HOME_PATH, EXTRINSIC_SETTINGS_FILENAME)
    SUN_POSITIONS_PATH = os.path.join(HOME_PATH, 'sun_positions')
    DARK_IMAGES_PATH = os.path.join(HOME_PATH, 'dark_images')

    UPLOAD_CMD = os.path.join(HOME_PATH, ".local/bin/dropbox_uploader.sh -k upload {capture_path} {upload_path}")


################################################################################
# Legacy stuff
################################################################################
PROXY_SERVER_KEY_FILE = os.path.expanduser(r'~/keys/cameranetwork.pem')


################################################################################
# Visualization stuff
################################################################################
LOS_PTS_NUM = 300
LOS_LENGTH = 8000
