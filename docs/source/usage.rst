********************************
Using the Camera Network Package
********************************

.. contents:: Table of Contents   


Client
======
After successful installation,
start the Client GUI by navigating to::

    cd cameranetwork/scripts_client

then run ``python camera_client.py``

You should now see

.. image:: images/GUI_on_start.png

after pressing on servers, you should see all connected cameras, in this case camera id 236.

.. image:: images/GUI_servers_with_camera.png

pressing on the camera ID should lead to the camera interface screen

.. image:: images/GUI_main_status.png


Camera (server)
===============
There are options to connect to the camera

#. Via Serial connection

#. Via SSH

#. Via GUI (as mentioned in the client section)


Proxy
=====
To connect to the proxy
-------------------------
``sudo ssh -i <path_to_key> ubuntu@<proxy_ip>``

.. note:: 
    ``sudo chmod 400 <path_to_private_key>``
    if encounter permission error

.. note::
    *<path_to_key>* is the path and name of the proxy's private key
    *<proxy_ip>* is defined in *global_settings.py*. Currently *3.123.49.101*

If this is the initial setup of the proxy server::

    python ./code/cameranetwork/scripts_proxy/start_proxy.py --log_level debug



Noticable stuff
---------------
*tunnel_port_<camera_id>.txt* stores the odroid's password and tunnel_port (random int between 20,000 and 30,000).

*/proxy_logs/cameralog_<date+time of ____ initialization>_proxy.txt* is a log.
Mainly shows Heartbeats from connected cameras and notification of message transmissions to/from the client.

Useful commands
---------------
- ``ps -ef | grep python``  to view running python processes (should see start_proxy.py!)
- Press ctrl+a then ctrl+d to detach the *start_proxy.py* from the terminal
- ``screen -ls`` to see detached processes. then``screen -r <name>`` to bring it back.
