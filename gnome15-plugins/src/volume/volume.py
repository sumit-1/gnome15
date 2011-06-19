#!/usr/bin/env python
 
#        +-----------------------------------------------------------------------------+
#        | GPL                                                                         |
#        +-----------------------------------------------------------------------------+
#        | Copyright (c) Brett Smith <tanktarta@blueyonder.co.uk>                      |
#        |                                                                             |
#        | This program is free software; you can redistribute it and/or               |
#        | modify it under the terms of the GNU General Public License                 |
#        | as published by the Free Software Foundation; either version 2              |
#        | of the License, or (at your option) any later version.                      |
#        |                                                                             |
#        | This program is distributed in the hope that it will be useful,             |
#        | but WITHOUT ANY WARRANTY; without even the implied warranty of              |
#        | MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the               |
#        | GNU General Public License for more details.                                |
#        |                                                                             |
#        | You should have received a copy of the GNU General Public License           |
#        | along with this program; if not, write to the Free Software                 |
#        | Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA. |
#        +-----------------------------------------------------------------------------+
 
import gnome15.g15screen as g15screen
import gnome15.g15util as g15util
import gnome15.g15theme as g15theme
import gnome15.g15driver as g15driver

import alsaaudio
import select
import os
import gtk
import time
import logging
import gobject
logger = logging.getLogger("volume")

from threading import Thread

# Plugin details - All of these must be provided
id="volume"
name="Volume Monitor"
description="Uses the M-Key lights as a volume meter. If your model has " + \
        " a screen, a page will also popup showing the current volume. " + \
        "You may choose the mixer that is monitored in the preferences for this plugin."
author="Brett Smith <tanktarta@blueyonder.co.uk>"
copyright="Copyright (C)2010 Brett Smith"
site="http://www.gnome15.org/"
has_preferences=True


''' 
This plugin displays a high priority screen when the volume is changed for a 
fixed number of seconds
'''

def create(gconf_key, gconf_client, screen):
    return G15Volume(screen, gconf_client, gconf_key)

def show_preferences(parent, driver, gconf_client, gconf_key):
    widget_tree = gtk.Builder()
    widget_tree.add_from_file(os.path.join(os.path.dirname(__file__), "volume.glade"))    
    dialog = widget_tree.get_object("VolumeDialog") 
    model = widget_tree.get_object("DeviceModel")   
    for mixer in alsaaudio.mixers():
        model.append([mixer])
    dialog.set_transient_for(parent)    
    g15util.configure_combo_from_gconf(gconf_client, gconf_key + "/mixer", "DeviceCombo", "Master", widget_tree)
    dialog.run()
    dialog.hide()
            
class G15Volume():
    
    def __init__(self, screen, gconf_client, gconf_key):
        self._screen = screen
        self._gconf_client = gconf_client
        self._gconf_key = gconf_key
        self._volume = 0.0
        self._volthread = None
        self._mute = False
        self.light_controls = None

    def activate(self):
        self._activated = True
        self._start_monitoring()
        self._notify_handler = self._gconf_client.notify_add(self._gconf_key, self._config_changed); 
    
    def deactivate(self):
        self._activated = False
        self._stop_monitoring()
        self._gconf_client.notify_remove(self._notify_handler)
        
    def destroy(self):
        pass
        
    ''' Functions specific to plugin
    ''' 
    def _start_monitoring(self):        
        self._volthread = VolumeThread(self)
        self._volthread.start()
    
    def _config_changed(self, client, connection_id, entry, args):    
        self._stop_monitoring()
        time.sleep(1.0)
        self._start_monitoring()
            
    def _stop_monitoring(self):
        if self._volthread != None:
            self._volthread._stop_monitoring()
        
    def _get_theme_properties(self):
        properties = {}
        icon = "audio-volume-muted"
        if not self._mute:
            if self._volume < 34:
                icon = "audio-volume-low"
            elif self._volume < 67:
                icon = "audio-volume-medium"
            else:
                icon = "audio-volume-high"
        else:
            properties [ "muted"] = True
        icon_path = g15util.get_icon_path(icon, self._screen.driver.get_size()[0])
        properties["state"] = icon
        properties["icon"] = icon_path
        properties["vol_pc"] = self._volume
        for i in range(0, ( self._volume / 10 ) + 1, 1):            
            properties["bar" + str(i)] = True
        return properties
            
    def _release_lights(self):
        self._screen.driver.release_mkey_lights(self.light_controls)
        self.light_controls = None
    
    def _popup(self):
        if not self._activated:
            logger.warning("Cannot popup volume when it is deactivated. This suggests the volume thread has not died.")
            return
        
        if not self.light_controls:
            self.light_controls = self._screen.driver.acquire_mkey_lights()
        else:
            self.lights_timer.cancel()
        self.lights_timer = g15util.schedule("ReleaseMKeyLights", 3.0, self._release_lights)
        
        page = self._screen.get_page(id)
        if page == None:
            if self._screen.driver.get_bpp() != 0:
                page = g15theme.G15Page(id, self._screen, priority=g15screen.PRI_HIGH, title="Volume", theme = g15theme.G15Theme(self), \
                                        theme_properties_callback = self._get_theme_properties)
                self._screen.delete_after(3.0, page)
                self._screen.add_page(page)
        else:
            self._screen.raise_page(page)
            self._screen.delete_after(3.0, page)
        
        
        mixer_name = self._gconf_client.get_string(self._gconf_key + "/mixer")
        if not mixer_name or mixer_name == "":
            mixer_name = "Master"
            
        logger.info("Opening mixer %s" % mixer_name)
        
        vol_mixer = alsaaudio.Mixer(mixer_name, cardindex=0)
        mute_mixer = None
        
        try :
        
            # Handle mute        
            mute = False
            mutes = None
            try :
                mutes = vol_mixer.getmute()
            except alsaaudio.ALSAAudioError:
                # Some pulse weirdness maybe?
                mute_mixer = alsaaudio.Mixer("PCM", cardindex=0)
                try :
                    mutes = mute_mixer.getmute()
                except alsaaudio.ALSAAudioError:
                    logger.warning("No mute switch found")
            if mutes != None:        
                for ch_mute in mutes:
                    if ch_mute:
                        mute = True
    
            
            # TODO  better way than averaging
            volumes = vol_mixer.getvolume()
        finally :
            vol_mixer.close()
            if mute_mixer:
                mute_mixer.close()
            
        total = 0
        for vol in volumes:
            total += vol
        volume = total / len(volumes)
        
        self._volume = volume              
        
        if self._volume > 90:
            self.light_controls.set_mkey_lights(g15driver.MKEY_LIGHT_MR | g15driver.MKEY_LIGHT_1 | g15driver.MKEY_LIGHT_2 | g15driver.MKEY_LIGHT_3)        
        elif self._volume > 75:
            self.light_controls.set_mkey_lights(g15driver.MKEY_LIGHT_1 | g15driver.MKEY_LIGHT_2 | g15driver.MKEY_LIGHT_3)        
        elif self._volume > 50:
            self.light_controls.set_mkey_lights(g15driver.MKEY_LIGHT_1 | g15driver.MKEY_LIGHT_2)        
        elif self._volume > 25:
            self.light_controls.set_mkey_lights(g15driver.MKEY_LIGHT_1)        
        else:
            self.light_controls.set_mkey_lights(0)
          
        self._mute = mute
        
        self._screen.redraw(page)
            
class VolumeThread(Thread):
    def __init__(self, volume):
        Thread.__init__(self)
        self.name = "VolumeThread"
        self.setDaemon(True)
        self._volume = volume
        
        mixer_name = volume._gconf_client.get_string(volume._gconf_key + "/mixer")
        if not mixer_name or mixer_name == "":
            mixer_name = "Master"
            
        logger.info("Opening mixer %s" % mixer_name)
        
        self._mixer = alsaaudio.Mixer(mixer_name, cardindex=0)
        self._poll_desc = self._mixer.polldescriptors()
        self._poll = select.poll()
        self._fd = self._poll_desc[0][0]
        self._event_mask = self._poll_desc[0][1]
        self._open = os.fdopen(self._fd)
        self._poll.register(self._open, select.POLLIN)
        self._stop = False
        
    def _stop_monitoring(self):
        self._stop = True
        self._open.close()
        self._mixer.close()
        
    def run(self):
        try :
            while not self._stop:
                if self._poll.poll(5):
                    if self._stop:
                        break
                    g15util.schedule("popupVolume", 0, self._volume._popup)
                    if not self._open.read():
                        break
        finally:
            try :
                self._poll.unregister(self._open)
            except :
                pass
            self._open.close()
 