# Garage Plugin
A garage monitor and control plugin for the Raspberry Pi based SIP irrigation control software

## Synopsis
A plugin for the Raspberry Pi based irrigation controll software  [SIP](https://github.com/Dan-in-CA/SIP).
This plugin, along with associated relay and sensor hardware, can monitor and control up to two garage doors, and send notifications on events like open, close, etc.

## Motivation
Wanting to monitor and control my garage doors, this initally started as a stand-alone project, written in python.
I had already installed some years back, a Raspberry Pi and OpenSprinkler board in my garage.
The SIP python code allows for plugins, and so it made sense to implement this as a SIP plugin, so my in-garage sprinkler controller RPi can double-duty as garage monitor and control.

## Code
The controller code is written as a plugin to SIP. It runs a separate thread, and reponds to GPIO events.
Directory layout of the repository shows where the individual files live.

The only "hack" (for now) is that the home.html file needs to be patched to show garage status on the SIP main page.
To apply the patch, log into your SIP RPi and apply it in the SIP/templates directory.

$ patch -p1 < home.patch

## Hardware used
To my Raspberry Pi and OpenSprinkler module setup, I added a two-relay module board, an overhead magnetic door sensor, and resistors for pullups and current limiting. See the diagrams section for how these parts are wired up and connected.
##### BOM
* SainSmart 2-Channel Relay Module 
  Link: http://a.co/61f2Ck4
* 10k resistors (x3)
* 1k resister (x1)
* Magnetic sensor suitable for garage door:
  Potter Amseco ODC-59A Overhead Door Switch
  Link: http://a.co/8qD3ivR


Usage
============
The plugin creates buttons in the plugins menu to activate the two relays.

Use the Garage Plugin settings page to change pin locations. Note that the defaults are chosen as unused pins in an OpenSprinkler setup.



Diagrams
============

### Block Diagram
Before converting this code-base into a SIP plugin, it was stand-alone. The stand-alone design started with the following diagram, which explains the control flow. This is essentially the same in the SIP plugin, however, the plugin GPIO pins, and timers, etc., are changeable via the plugin's settings.
![alt text](https://cdn.rawgit.com/andersix/sip_garage_plugin/master/doc/GaragePi.svg)


(hardware wiring/schematic diagram here)


