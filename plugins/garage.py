#!/usr/bin/env python
#
# Control and monitor Garage doors using Raspberry Pi
#
# Copyright (C) 2018  Anders Knudsen
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import os
import sys
import traceback
import web  # web.py framework
import gv  # Get access to SIP's settings
from urls import urls  # Get access to SIP's URLs
from sip import template_render  # Needed for working with web.py templates
from webpages import ProtectedPage  # Needed for security
import json  # for working with data file
import time
from helpers import jsave
from helpers import timestr
from helpers import restart
from gpio_pins import GPIO
from random import randint
from threading import Thread

#
# TODO FIXME : create a new email method to use Google apps API instead of smtplib
#              See how-to here: https://developers.google.com/gmail/api/quickstart/python
#                               https://stackoverflow.com/questions/25944883/how-to-send-an-email-through-gmail-without-enabling-insecure-access
#
from email import Encoders
import smtplib
from email.MIMEMultipart import MIMEMultipart
from email.MIMEBase import MIMEBase
from email.MIMEText import MIMEText

try:
    from twilio.rest import TwilioRestClient
    TWILIO_EN = True
except:
    print "From Garage Plugin:"
    print "Twilio lib not installed. Twilio SMS send will not work"
    print "Use pip to install the twilio python library, or"
    print "download the twilio-python library from http://twilio.com/docs/libraries"
    TWILIO_EN = False

# TODO FIXME : add support for "pi" gpio_pins. Only supporting GPIO for now...
# if gv.use_pigpio:
#     from gpio_pins import pi
# else:
#     from gpio_pins import GPIO


#
# at first use, data-file does not exist, and plugin will use defaults:
#
DATA_FILE = "./data/garage.json"

#
# Plugin menu entries ['Menu Name', 'URL'], (Optional)
#
gvmenu_settings = ['Garage Doors Settings', '/garage-s']
gvmenu_button1  = ['Garage Button 1', '/garage-b1']
gvmenu_button2  = ['Garage Button 2', '/garage-b2']

plugin_urls = [
    '/garage-b1',   'plugins.garage.garage_button_1',
    '/garage-b2',   'plugins.garage.garage_button_2',
    '/garage-s',    'plugins.garage.settings',
    '/garage-save', 'plugins.garage.save_settings'
]

###############################################################################
# Garage controller thread
#
class GarageControl(Thread):
    def __init__(self, gpio):
        Thread.__init__(self)
        # add plugin menu items:
        gv.plugin_menu.append(gvmenu_settings)
        gv.plugin_menu.append(gvmenu_button1)
        gv.plugin_menu.append(gvmenu_button2)
        # add urls:
        urls.extend(plugin_urls)
        # remaining plugin init:
        self.daemon = True
        self.name = 'garage'
        self.gpio = gpio
        self.status = ''
        self._sleep_time = 0
        self._door_state = {"1":"UNKNOWN", "2":"UNKNOWN"}
        self._event_time = 0  # events are buttons and door sensors
        self.settings = {}
        self.subject = "Garage"  # TODO add subject to settings file
        self.tp = 10  # seconds to pause thread loop
        self.nag_limit = 6  # will stop after this many nag notifications. TODO: add this to settings
        self.start()

    # TODO FIXME : this status "string" should be replaced with a logging mechanism
    #              so we get it outta memory!
    def add_status(self, msg, debug=True):
        _status = 'STATUS: ' + time.strftime("%d.%m.%Y at %H:%M:%S", time.localtime(time.time())) + ': ' + msg
        if self.status:
            self.status += '\n' + _status
        else:
            self.status = _status
        if debug:
            print(_status)

    def try_notify(self, subject, text, when=None, attachment=None):
        """
        This method will send a notification if enabled in settings.
        By default, the notifications are disabled until enabled in
        settings.
        Here we have support for email notification and Twilio SMS.
        Note: with email notifcation, you can generally send SMS via
        a cell provider's SMS gateway.
        """
        #self.status = ''
	mail_en = False if self.settings['mail_en'] == 'off' else True
	twil_en = False if self.settings['twil_en'] == 'off' else True
        if when is None:
            when = time.localtime(time.time())
        _time = time.strftime("%d.%m.%Y at %H:%M:%S", when)
        text = text + "\nOn " + _time
        if mail_en:
            try:
                send_email(subject, text, attachment)  # send email with attachment from
                self.add_status('Email sent: ' + text)
            except Exception as err:
                self.add_status('Email not sent! ' + str(err))
        if twil_en:
            try:
                send_sms(self.settings['twil_sid'], self.settings['twil_atok'], self.settings['twil_to'], self.settings['twil_from'], text)
                self.add_status('SMS sent: ' + text)
            except Exception as err:
                self.add_status('SMS not sent! ' + str(err))

    def setup_gpio(self, s):
        """
        Sets up GPIO pins for relays and sensors as set in garage settings.
        Supports 'n' relays and sensors, however, the HTML in templates/garage.html
        is only coded for up to 2 relays and sensors (the HTML could be improved.)
        """
        r = s['relay']
        for n in r:
            pin = r[n]['pin']  # relay GPIO pin
            pol = r[n]['pol']  # relay GPIO polarity
            prm = r[n]['prm']  # relay permit open
            typ = r[n]['typ']  # relay type: '1' is a door, '0' is other
            if pin:
                try:
                    self.gpio.setup(pin, self.gpio.OUT)
                    self.gpio.output(pin, self.gpio.LOW ^ pol)
                    self.add_status("Adding Relay %s: output-pin(%0d); polarity(%0d); permit-open(%r); is-a-door(%r)" % (n, pin, pol, prm, typ))
                except:
                    self.add_status("Error setting GPIO for Relay %s" % n)
        s = s['sensor']
        for n in s:
            pin = s[n]['pin']  # sensor GPIO pin
            pud = s[n]['pud']  # sensor GPIO pull-up(1) or pull-down(0) enable
            if pin:
                try:
                    gpud = self.gpio.PUD_UP if pud else self.gpio.PUD_DOWN 
                    self.add_status("Enabling input sensor %s on gpio pin: %0d; PUD(%0d)" % (n, pin, pud))
                    self.gpio.setup(pin, self.gpio.IN, pull_up_down=gpud)
                    self._door_state[n] = self.get_door_state(pin)
                    self.add_status("Initial door %s sensor state is %s" % (n, self._door_state[n]))
                    self.add_status("Adding door %s sensor event detection" % n)
                    self.gpio.add_event_detect(pin, self.gpio.BOTH, callback=self.door_event, bouncetime=1000)
                except:
                    self.add_status("Error setting GPIO for Sensor %s" % n)
       
        
    
    def get_door_state(self, pin):
        try:
            if self.gpio.input(pin) == 0:
                return "CLOSED"
            # If the pin state is '1', we say it's OPEN, but it could be OPENING or CLOSING
            # To improve, I could add a second sensor to indicate when the door is fully open.
            if self.gpio.input(pin) == 1:
                return "OPEN"
        except:
            return "ERROR"
    
    def door_event(self,channel):
        """
        This is the GPIO event callback function. It runs in a separate thread,
        and is called anytime the configured sensor changes status. When called,
        it changes the door status so we can act on it.
        """
        self._event_time = time.time()
        _door_state = self.get_door_state(channel)
        self.add_status("Door sensor triggered on channel %0d" % channel)
        self.add_status("DEBUG: Door on channel %s is %s" % (channel, _door_state))
        s = self.settings['sensor']
        for n in s:
            pin = s[n]['pin']
            pud = s[n]['pud']
            if(channel == pin):
                time.sleep(0.250)
                _door_state = self.get_door_state(channel)
                if not (self._door_state[n] == _door_state):
                    self._door_state[n] = _door_state
                    #self._door_state[n] = self.get_door_state(channel)
                    self.add_status("Door %s is %s" % (n, self._door_state[n]))
	            if self.settings['ntfy_gev'] == 'on':
                        self.try_notify(self.subject, "\nDoor %s %s" % (n, self._door_state[n]))
                else:
                    self.add_status("DEBUG: Door status unchanged, Door %s is %s" % (n, self._door_state[n]))
                break
    
    def toggle_relay(self, pin, pol, hold_time):
        self.gpio.output(pin, self.gpio.HIGH ^ pol)
        time.sleep(hold_time)
        self.gpio.output(pin, self.gpio.LOW ^ pol)

    def press_button(self, button):
        """
        'Presses' the button using the configured relay. Relay could be a door,
        or other button like the garage light button.
        """
        try:
            _rp = self.settings['relay'][button]['pin']
            _rx = self.settings['relay'][button]['pol']
            _po = self.settings['relay'][button]['prm']
            _rd = self.settings['relay'][button]['typ']
            _dy = 0.2  # relay toggle delay
            if not _rd:  # if not a door, allow toggle anytime
                self.toggle_relay(_rp,_rx,_dy)
                self._door_state[button] = 'NOT_A_DOOR'
                self.add_status("Toggled Relay %s" % button)
            else:  # otherwise, relay is a door, so honor allow-open permission
                if not _po and (self._door_state[button] == 'CLOSED' or self._door_state[button] == 'CLOSING'):
                    self.add_status("Opening Door %s not permitted." % button)
                elif self._door_state[button] == 'OPEN' or self._door_state[button] == 'OPENING':
                    self.toggle_relay(_rp,_rx,_dy)
                    self._door_state[button] = 'CLOSING'
                    self.add_status("Closing Door %s" % button)
                elif self._door_state[button] == 'CLOSED' or self._door_state[button] == 'CLOSING':
                    self.toggle_relay(_rp,_rx,_dy)
                    self._door_state[button] = 'OPENING'
                    self.add_status("Opening Door %s" % button)
                else:
                    self.add_status("Door %s state is unknown..." % button)
            self._event_time = time.time()
        except:
            self.add_status("Error toggling relay %s" % button)

    def run(self):
        t_start = gv.gc_start     # Keep thread start time (used in case thread restarts)
        time.sleep(self.tp + 10)  # Sleep some time to prevent printing before startup information.
                                  # This time delay should match, or exceed this loop sleep time so
                                  # that a program restart does not create multiple gpio event threads.
        self.add_status('Garage plugin starting...')
        self.settings = get_data()
        self.setup_gpio(self.settings)

        s = self.settings['sensor']

        gv.gc_started = True

        while True:
            try:
                gv.gc_door_state = self._door_state
                # Monitor door state and notify.
                for n in s:
                    pin = s[n]['pin']  # sensor pin
                    if(pin):
                        if self._door_state[n] == "CLOSING":
                            closing_time = time.time()
                            self.add_status("Detected door {} is closing at {}.".format(n, closing_time) )
                            # notify if "door closing" takes too long...
                            while(self._door_state[n] == "CLOSING"):
                                closing_time = time.time()
                                self.add_status("Detected door {} is still closing at {}.".format(n, closing_time) )
                                if closing_time - self._event_time > 60:  # if taking too long, assume door is OPEN
                                    self.try_notify(self.subject, "Garage Door {} is taking a long time to close. Assuming it's still OPEN.".format(n) )
                                    self._event_time = time.time()
                                    self.nag_limit = 6  # reset nag timer limit
                                    self._door_state[n] = "OPEN"
                                time.sleep(1)
#
# TODO FIXME : * notify once of door open if gcd is 'on', and nag time is zero
#              * add a door is open "nag count", that is, stop nagging after "count" times.
#
# TODO : maybe add "I'm home" button/url, so you can indicate garage door open for long time is OK.
#
                        if self._door_state[n] == "OPEN":
                            if self.settings['ntfy_gdo'][0] == 'on' and self.settings['ntfy_gdo'][1]:
                                open_time = time.time()
                                if (open_time - self._event_time > self.settings['ntfy_gdo'][1]) and self.nag_limit > 0:
                                    self._event_time = open_time
                                    if self.nag_limit > 1:
                                        self.try_notify(self.subject, "Garage Door {} is still Open ({})".format(n,self.nag_limit))
                                    else:
                                        self.try_notify(self.subject, "OK. I'll stop nagging, but Garage Door {} is still Open".format(n,self.nag_limit))
                                    if self.nag_limit > 0:
                                        self.nag_limit -= 1
                            #elif self.settings['ntfy_gdo'][0] == 'on' and self.settings['ntfy_gdo'][1] == 0:
                            #        self.try_notify(self.subject, "Garage Door %s is Open" % n)
#
# TODO FIXME : * notify once of door closed if gcd is 'on', and nag time is zero
#              * add a door is closed "nag count", that is, stop nagging after "count" times.
#
                        if self._door_state[n] == "CLOSED":
                            self.nag_limit = 6  # reset nag timer limit
                            if self.settings['ntfy_gdc'][0] == 'on' and self.settings['ntfy_gdc'][1]:
                                close_time = time.time()
                                if close_time - self._event_time > self.settings['ntfy_gdc'][1]:
                                    self._event_time = close_time
                                    self.try_notify(self.subject, "Garage Door %s is still Closed" % n)
                            #elif self.settings['ntfy_gdc'][0] == 'on' and self.settings['ntfy_gdc'][1] == 0:
                            #        self.try_notify(self.subject, "Garage Door %s Closed" % n)
#
# TODO FIXME : * maybe add an option to close door after being open for a specified time
#
            except Exception:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                err_string = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
                self.add_status('Garage Control plugin encountered error:\n' + err_string)
                #self._sleep(3600)
                time.sleep(3600)

            #
            # TODO FIXME : not sure why this happens, but occationally, the plugin is re-loaded/re-started
            #              by the main SIP plugin code (I think) and so I added this code to keep track of
            #              the thread start time, so the old thread is killed. Hack? Bug? Help...
            #
            if not t_start == gv.gc_start:  # Program restarted, so clean-up GPIO and stop thread
                for n in s:
                    pin = s[n]['pin']
                    if pin:
                        self.gpio.remove_event_detect(pin)
                print(time.strftime("%c") + ", Exiting Thread\n") 
                self.add_status(time.strftime("%c") + ", Exiting Thread\n") 
                # remove menu items/urls if we restart, so we don't keep expanding the lists!
                gv.plugin_menu.remove(gvmenu_settings)
                gv.plugin_menu.remove(gvmenu_button1)
                gv.plugin_menu.remove(gvmenu_button2)
                for v in plugin_urls:
                    if v in urls:
                        urls.remove(v)
                return
            # pause thread loop for 'n' seconds
            #self._sleep(self.tp)
            time.sleep(self.tp)



#
# Keep time when this plugin started its thread. Then if it restarts, any 
# running threads can know to stop, so we don't get multiples running.
gv.gc_started = False
gv.gc_start = time.time()

# Start an instance of Garage controller thread
# TODO FIXME : add support for "pi" gpio_pins. Only supporting GPIO for now...
#if gv.use_pigpio:
#    controller = GarageControl(pi)
#else:
#    controller = GarageControl(GPIO)
controller = GarageControl(GPIO)


################################################################################
# OSPi web page classen

class settings(ProtectedPage):
    """
    Load an html page for entering plugin settings.
    """
    def GET(self):
        settings = get_data()
        return template_render.garage(settings)  # open settings page


class save_settings(ProtectedPage):
    """
    Save user input to json file.
    Will create or update file when SUBMIT button is clicked
    CheckBoxes only appear in qdict if they are checked,
    so test and set accordingly.
    """
    def GET(self):
        qdict = web.input()  # Dictionary of values returned as query string from settings page.

        # print "save_settings: qdict:"
        # print qdict  # for testing
        # print

        if 'relay1_pin' in qdict and qdict['relay1_pin'] != '':
            controller.settings['relay']['1']['pin'] = int(qdict['relay1_pin'])

        if 'relay1_pol' not in qdict:
            controller.settings['relay']['1']['pol'] = 0
        else:
            controller.settings['relay']['1']['pol'] = 1

        if 'relay1_opa' not in qdict:
            controller.settings['relay']['1']['prm'] = 0
        else:
            controller.settings['relay']['1']['prm'] = 1

        if 'relay1_iad' not in qdict:
            controller.settings['relay']['1']['typ'] = 0
        else:
            controller.settings['relay']['1']['typ'] = 1

        if 'relay2_pin' in qdict and qdict['relay2_pin'] != '':
            controller.settings['relay']['2']['pin'] = int(qdict['relay2_pin'])

        if 'relay2_pol' not in qdict:
            controller.settings['relay']['2']['pol'] = 0
        else:
            controller.settings['relay']['2']['pol'] = 1

        if 'relay2_opa' not in qdict:
            controller.settings['relay']['2']['prm'] = 0
        else:
            controller.settings['relay']['2']['prm'] = 1

        if 'relay2_iad' not in qdict:
            controller.settings['relay']['2']['typ'] = 0
        else:
            controller.settings['relay']['2']['typ'] = 1

        if 'sensor1_pin' in qdict and qdict['sensor1_pin'] != '':
            controller.settings['sensor']['1']['pin'] = int(qdict['sensor1_pin'])

        if 'sensor1_pud' not in qdict:
            controller.settings['sensor']['1']['pud'] = 0
        else:
            controller.settings['sensor']['1']['pud'] = 1

        if 'sensor2_pin' in qdict and qdict['sensor2_pin'] != '':
            controller.settings['sensor']['2']['pin'] = int(qdict['sensor2_pin'])

        if 'sensor2_pud' not in qdict:
            controller.settings['sensor']['2']['pud'] = 0
        else:
            controller.settings['sensor']['2']['pud'] = 1


        if 'mail_en' not in qdict:
            controller.settings['mail_en'] = 'off'
        else:
            controller.settings['mail_en'] = qdict['mail_en']

        if 'mail_usr' in qdict and qdict['mail_usr'] != '':
            controller.settings['mail_usr'] = qdict['mail_usr']

        #
        # note: I recommend you use a burner gmail account, that can
        # forward notifications on to other emails, or send to
        # a mobile provider's SMS gateway address, i.e.,
        # 5555555555@vtext.com, etc. See this page for a good list:
        # https://en.wikipedia.org/wiki/SMS_gateway
        #
        if 'mail_pwd' in qdict and qdict['mail_pwd'] != '':
            controller.settings['mail_pwd'] = qdict['mail_pwd']

        if 'mail_adr' in qdict and qdict['mail_adr'] != '':
            controller.settings['mail_adr'] = qdict['mail_adr']

        if 'ntfy_log' not in qdict:
            controller.settings['ntfy_log'] = 'off'
        else:
            controller.settings['ntfy_log'] = qdict['ntfy_log']

        if 'ntfy_rain' not in qdict:
            controller.settings['ntfy_rain'] = 'off'
        else:
            controller.settings['ntfy_rain'] = qdict['ntfy_rain']

        if 'ntfy_run' not in qdict:
            controller.settings['ntfy_run'] = 'off'
        else:
            controller.settings['ntfy_run'] = qdict['ntfy_run']

        if 'ntfy_gev' not in qdict:
            controller.settings['ntfy_gev'] = 'off'
        else:
            controller.settings['ntfy_gev'] = qdict['ntfy_gev']

        if 'ntfy_gdo[0]' not in qdict:
            controller.settings['ntfy_gdo'][0] = 'off'
        else:
            controller.settings['ntfy_gdo'][0] = qdict['ntfy_gdo[0]']
        controller.settings['ntfy_gdo'][1] = int(qdict['ntfy_gdo[1]'])
        
        if 'ntfy_gdc[0]' not in qdict:
            controller.settings['ntfy_gdc'][0] = 'off'
        else:
            controller.settings['ntfy_gdc'][0] = qdict['ntfy_gdc[0]']
        controller.settings['ntfy_gdc'][1] = int(qdict['ntfy_gdc[1]'])
        
        if 'twil_en' not in qdict:
            controller.settings['twil_en'] = 'off'
        else:
            controller.settings['twil_en'] = qdict['twil_en']
        if 'twil_sid' in qdict and qdict['twil_sid'] != '':
            controller.settings['twil_sid'] = qdict['twil_sid']
        if 'twil_atok' in qdict and qdict['twil_atok'] != '':
            controller.settings['twil_atok'] = qdict['twil_atok']
        if 'twil_to' in qdict and qdict['twil_to'] != '':
            controller.settings['twil_to'] = qdict['twil_to']
        if 'twil_from' in qdict and qdict['twil_from'] != '':
            controller.settings['twil_from'] = qdict['twil_from']


        # don't save status in the data file
        controller.settings['status'] = ""
        jsave(controller.settings, 'garage');
        controller.settings['status'] = controller.status
        raise web.seeother('/restart')  # restart after settings change required


class garage_button_1(ProtectedPage):
    def GET(self):
        controller.press_button('1')
        raise web.seeother('/')  # return to home page

class garage_button_2(ProtectedPage):
    def GET(self):
        controller.press_button('2')
        raise web.seeother('/')  # return to home page


################################################################################
# helper methods
#
def get_data():
    """
    Get settings from data/garage.json, or use defaults if the
    data file does not exist.

    Note: OSPi uses GPIO BOARD mode
          (i.e., pin 16 == gpio 23), so set pins accordingly.
    Default supports 2 relays, and 2 sensors. Expand as needed.
    """
    # default relay/sensor settings using unused ospi gpio pins
    defaults = {
        'relay'      : { '1':{'pin':16, 'pol':1, 'prm':1, 'typ':1},
                         '2':{'pin':18, 'pol':1, 'prm':1, 'typ':0} },
        'sensor'     : { '1':{'pin':22, 'pud':1},
                         '2':{'pin':0 , 'pud':1} },
        'mail_en'    : 'off',
        'mail_usr'   : '',
        'mail_pwd'   : '',
        'mail_adr'   : '',
        'ntfy_log'   : 'off',
        'ntfy_rain'  : 'off',
        'ntfy_run'   : 'off',
        'ntfy_gev'   : 'off',
        'ntfy_gdo'   : [ 'on', 300 ],
        'ntfy_gdc'   : [ 'off', 0 ],
        'twil_en'    : 'off',
        'twil_sid'   : '',
        'twil_atok'  : '',
        'twil_to'    : '',
        'twil_from'  : '',
        'status'     : controller.status
    }
    settings = {}
    if not controller.settings:
        settings = defaults
    else:
        # if settings exist don't overwrite with defaults
        settings = controller.settings
        # except for status
        settings['status'] = controller.status

    try:
        with open(DATA_FILE, 'r') as fh:  # Read settings from json file if it exists
            try:
                _data_items = json.load(fh)
                for key, value in _data_items.iteritems():
                    if key in settings:
                        settings[key] = value
            except ValueError as e:
                print("Garage pluging couldn't parse data file:", e)
            finally:
                fh.close()
        print("Garage Plugin settings data file loaded")
        settings['status'] = controller.status
        #print settings
    except IOError as e:
        print("Using Garage Plugin default settings: ", e)
        #print settings
    return settings


def send_email(subject, text, attach=None):
    """
    Send email with with optional attachments
    If we have attachments, we send a MIME message,
    otherwise we send plain text.
    Note: If using gmail, using SMTPLIB is deprecated.
          You can, however, allow this "less secure"
          access to your gmail account by enabling it:
          https://support.google.com/accounts/answer/6010255?hl=en          
    """
    settings = controller.settings
    if settings['mail_usr'] != '' and settings['mail_pwd'] != '' and settings['mail_adr'] != '':
        mail_user = settings['mail_usr']  # User name
        mail_from = gv.sd['name']       # OSPi name
        mail_pwd = settings['mail_pwd']   # User password
        mail_to = settings['mail_adr']
        #--------------
        if attach is not None:
            msg = MIMEMultipart()
            msg['From'] = mail_from
            msg['To'] = mail_to
            msg['Subject'] = subject
            msg.attach(MIMEText(text))
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(open(attach, 'rb').read())
            Encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment; filename="%s"' % os.path.basename(attach))
            msg.attach(part)
            message = msg.as_string()
        else:
            message = """From: %s\nTo: %s\nSubject: %s\n\n%s
            """ % (mail_from, ", ".join(mail_to), subject, text)
        #----------
        mailServer = smtplib.SMTP("smtp.gmail.com", 587)  # TODO FIXME : put server address and port into settings
        mailServer.ehlo()
        mailServer.starttls()
        mailServer.ehlo()
        mailServer.login(mail_user, mail_pwd)
        mailServer.sendmail(mail_from, settings['mail_adr'], message)  # name + e-mail address in the From: field
        mailServer.close()
    else:
        raise Exception('E-mail settings not properly configured!')

def send_sms(account_sid, auth_token, num_to, num_from, msg):
    if account_sid != '' and auth_token != '' and num_to != '' and num_from != '':
        # send SMS via Twilio
        try:
            client = TwilioRestClient(account_sid, auth_token)
            message = client.messages.create(to=num_to, from_=num_from, body=msg)
            print message
        except:
            raise Exception('Twilio send failed')
    else:
        raise Exception('Twilio settings not properly configured!')

