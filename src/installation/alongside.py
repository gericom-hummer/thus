#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  installation_alongside.py
#
#  This file was forked from Cnchi (graphical installer from Antergos)
#  Check it at https://github.com/antergos
#
#  Copyright 2013 Antergos (http://antergos.com/)
#  Copyright 2013 Manjaro (http://manjaro.org)
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.

import xml.etree.ElementTree as etree

from gi.repository import Gtk, Gdk

import sys
import os
import canonical.misc as misc
import logging
import show_message as show
import bootinfo
import subprocess

# To be able to test this installer in other systems
# that do not have pyparted3 installed
try:
    import parted
except:
    print("Can't import parted module! This installer won't work.")

# Insert the src/parted directory at the front of the path.
base_dir = os.path.dirname(__file__) or '.'
parted_dir = os.path.join(base_dir, 'parted3')
sys.path.insert(0, parted_dir)

import parted3.partition_module as pm
import parted3.fs_module as fs

from installation import process as installation_process

_next_page = "user_info"
_prev_page = "installation_ask"

# leave at least 5GB for Manjaro when shrinking
_minimum_space_for_manjaro = 5000


class InstallationAlongside(Gtk.Box):
    def __init__(self, params):
        self.title = params['title']
        self.forward_button = params['forward_button']
        self.backwards_button = params['backwards_button']
        self.callback_queue = params['callback_queue']
        self.settings = params['settings']
        self.alternate_package_list = params['alternate_package_list']
        self.testing = params['testing']

        super().__init__()
        self.ui = Gtk.Builder()
        self.ui_dir = self.settings.get('ui')
        self.ui.add_from_file(os.path.join(self.ui_dir, "installation_alongside.ui"))

        self.ui.connect_signals(self)

        self.label = self.ui.get_object('label_info')

        self.treeview = self.ui.get_object("treeview1")
        self.treeview_store = None
        self.prepare_treeview()
        self.populate_treeview()

        # Init dialog slider
        self.init_slider()

        super().add(self.ui.get_object("installation_alongside"))

    def init_slider(self):
        dialog = self.ui.get_object("shrink-dialog")
        slider = self.ui.get_object("scale")

        slider.set_name("myslider")
        path = os.path.join(self.settings.get("data"), "css", "scale.css")

        self.available_slider_range = [0, 0]

        if os.path.exists(path):
            with open(path, "rb") as css:
                css_data = css.read()

            provider = Gtk.CssProvider()

            try:
                provider.load_from_data(css_data)

                Gtk.StyleContext.add_provider_for_screen(
                    Gdk.Screen.get_default(), provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
            except:
                logging.exception(_("Can't load %s css") % path)

        #slider.add_events(Gdk.EventMask.SCROLL_MASK)

        slider.connect("change-value", self.slider_change_value)

        '''
        slider.connect("value_changed",
                self.main.on_volume_changed)
        slider.connect("button_press_event",
                self.on_scale_button_press_event)
        slider.connect("button_release_event",
                self.on_scale_button_release_event)
        slider.connect("scroll_event",
                self.on_scale_scroll_event)
        '''

    def slider_change_value(self, slider, scroll, value):
        if value <= self.available_slider_range[0] or \
           value >= self.available_slider_range[1]:
            return True
        else:
            slider.set_fill_level(value)
            self.update_ask_shrink_size_labels(value)
            return False

    def translate_ui(self):
        txt = _("Choose which OS you want to install Manjaro next to")
        txt = '<span size="large">%s</span>' % txt
        self.label.set_markup(txt)

        txt = _("Manjaro alongside another OS")
        txt = "<span weight='bold' size='large'>%s</span>" % txt
        self.title.set_markup(txt)

        txt = _("Install now!")
        self.forward_button.set_label(txt)

    def prepare(self, direction):
        self.translate_ui()
        self.show_all()
        self.forward_button.set_sensitive(False)

    def store_values(self):
        self.start_installation()
        return True

    def get_prev_page(self):
        return _prev_page

    def get_next_page(self):
        return _next_page

    def prepare_treeview(self):
        ## Create columns for our treeview
        render_text = Gtk.CellRendererText()

        col = Gtk.TreeViewColumn(_("Device"), render_text, text=0)
        self.treeview.append_column(col)

        col = Gtk.TreeViewColumn(_("Detected OS"), render_text, text=1)
        self.treeview.append_column(col)

        col = Gtk.TreeViewColumn(_("Filesystem"), render_text, text=2)
        self.treeview.append_column(col)

    @misc.raise_privileges
    def populate_treeview(self):
        if self.treeview_store is not None:
            self.treeview_store.clear()

        self.treeview_store = Gtk.TreeStore(str, str, str)

        oses = {}
        oses = bootinfo.get_os_dict()

        self.partitions = {}

        try:
            device_list = parted.getAllDevices()
        except:
            txt = _("pyparted3 not found!")
            logging.error(txt)
            show.fatal_error(txt)
            device_list = []

        for dev in device_list:
            ## avoid cdrom and any raid, lvm volumes or encryptfs
            if not dev.path.startswith("/dev/sr") and \
               not dev.path.startswith("/dev/mapper"):
                try:
                    disk = parted.Disk(dev)
                    # create list of partitions for this device (p.e. /dev/sda)
                    partition_list = disk.partitions

                    for p in partition_list:
                        if p.type != pm.PARTITION_EXTENDED:
                            ## Get filesystem
                            fs_type = ""
                            if p.fileSystem and p.fileSystem.type:
                                fs_type = p.fileSystem.type
                            if "swap" not in fs_type:
                                if p.path in oses:
                                    row = [p.path, oses[p.path], fs_type]
                                else:
                                    row = [p.path, _("unknown"), fs_type]
                                self.treeview_store.append(None, row)
                        self.partitions[p.path] = p
                except Exception as e:
                    txt = _("Unable to create list of partitions for alongside installation.")
                    logging.warning(txt)
                    #show.warning(txt)

        # assign our new model to our treeview
        self.treeview.set_model(self.treeview_store)
        self.treeview.expand_all()

    def on_treeview_cursor_changed(self, widget):
        selection = self.treeview.get_selection()

        if not selection:
            return

        model, tree_iter = selection.get_selected()

        if tree_iter is None:
            return

        self.row = model[tree_iter]

        partition_path = self.row[0]
        other_os_name = self.row[1]

        self.min_size = 0
        self.max_size = 0
        self.new_size = 0

        try:
            subprocess.call(["mount", partition_path, "/mnt"], stderr=subprocess.DEVNULL)
            x = subprocess.check_output(['df', partition_path]).decode()
            subprocess.call(["umount", "-l", "/mnt"], stderr=subprocess.DEVNULL)
            x = x.split('\n')
            x = x[1].split()
            self.max_size = int(x[1]) / 1000
            self.min_size = int(x[2]) / 1000
        except subprocess.CalledProcessError as e:
            txt = "CalledProcessError.output = %s" % e.output
            logging.error(txt)
            show.fatal_error(txt)

        if self.min_size + _minimum_space_for_manjaro < self.max_size:
            self.new_size = self.ask_shrink_size(other_os_name)
        else:
            txt = _("Can't shrink the partition (maybe it's nearly full?)")
            logging.error(txt)
            show.error(txt)
            return

        if self.new_size > 0 and self.is_room_available():
            self.forward_button.set_sensitive(True)
        else:
            self.forward_button.set_sensitive(False)

    def update_ask_shrink_size_labels(self, new_value):
        label_other_os_size = self.ui.get_object("label_other_os_size")
        label_other_os_size.set_markup(str(int(new_value)) + " MB")

        label_manjaro_size = self.ui.get_object("label_manjaro_size")
        label_manjaro_size.set_markup(str(int(self.max_size - new_value)) + " MB")

    def ask_shrink_size(self, other_os_name):
        dialog = self.ui.get_object("shrink-dialog")

        slider = self.ui.get_object("scale")

        # leave space for Manjaro
        self.available_slider_range = [self.min_size, self.max_size - _minimum_space_for_manjaro]

        slider.set_fill_level(self.min_size)
        slider.set_show_fill_level(True)
        slider.set_restrict_to_fill_level(False)
        slider.set_range(0, self.max_size)
        slider.set_value(self.min_size)
        slider.set_draw_value(False)

        label_other_os = self.ui.get_object("label_other_os")
        txt = "<span weight='bold' size='large'>%s</span>" % other_os_name
        label_other_os.set_markup(txt)

        label_manjaro = self.ui.get_object("label_manjaro")
        txt = "<span weight='bold' size='large'>Manjaro</span>"
        label_manjaro.set_markup(txt)

        self.update_ask_shrink_size_labels(self.min_size)

        response = dialog.run()

        value = 0

        if response == Gtk.ResponseType.OK:
            value = int(slider.get_value()) + 1

        dialog.hide()

        return value

    def is_room_available(self):
        partition_path = self.row[0]
        otherOS = self.row[1]
        fs_type = self.row[2]

        # what if path is sda10 (two digits) ? this is wrong
        device_path = self.row[0][:-1]

        new_size = self.new_size

        logging.debug("partition_path: %s" % partition_path)
        logging.debug("device_path: %s" % device_path)
        logging.debug("new_size: %s" % new_size)

        # Find out how many primary partitions device has, and also
        # if there's already an extended partition

        extended_path = ""
        primary_partitions = []

        for path in self.partitions:
            if device_path in path:
                p = self.partitions[path]
                if p.type == pm.PARTITION_EXTENDED:
                    extended_path = path
                elif p.type == pm.PARTITION_PRIMARY:
                    primary_partitions.append(path)

        primary_partitions.sort()

        logging.debug("extended partition: %s" % extended_path)
        logging.debug("primary partitions: %s" % primary_partitions)

        if len(primary_partitions) >= 4:
            txt = _("There are too many primary partitions, can't create a new one")
            logging.error(txt)
            show.error(txt)
            return False

        self.extended_path = extended_path

        return True

    def start_installation(self):
        # Alongside method shrinks selected partition
        # and creates root and swap partition in the available space

        if self.is_room_available() is False:
            return

        partition_path = self.row[0]
        otherOS = self.row[1]
        fs_type = self.row[2]

        # what if path is sda10 (two digits) ? this is wrong
        device_path = self.row[0][:-1]

        #re.search(r'\d+$', self.row[0])

        new_size = self.new_size

        # first, shrink filesystem
        res = fs.resize(partition_path, fs_type, new_size)
        if res:
            print("Filesystem on " + partition_path + " shrunk.\nWill recreate partition now on device " + device_path + " partition " + partition_path)
            # destroy original partition and create a new resized one
            res = pm.split_partition(device_path, partition_path, new_size)
        else:
            txt = _("Can't shrink %s(%s) filesystem") % (otherOS, fs_type)
            logging.error(txt)
            show.error(txt)
            return

        if res:
            print("Partition " + partition_path + " shrink complete.")
        else:
            txt = _("Can't shrink %s(%s) partition") % (otherOS, fs_type)
            logging.error(txt)
            show.error(txt)
            print("*** FILESYSTEM IN UNSAFE STATE ***\nFilesystem shrink succeeded but partition shrink failed.")
            return

        print("NOT IMPLEMENTED YET: perform installation")
        '''
        # Prepare info for installer_process
        mount_devices = {}
        mount_devices["/"] =
        mount_devices["swap"] =

        root = mount_devices["/"]
        swap = mount_devices["swap"]

        fs_devices = {}
        fs_devices[root] = "ext4"
        fs_devices[swap] = "swap"
        fs_devices[partition_path] = self.row[2]


        # TODO: Ask where to install the bootloader (if the user wants to install it)

        # Ask bootloader type
        import bootloader
        bl = bootloader.BootLoader(self.settings)
        bl.ask()

        if self.settings.get('install_bootloader'):
            self.settings.set('bootloader_location', mount_devices["/"])
            logging.info(_("Manjaro will install the bootloader of type %s in %s") % \
                (self.settings.get('bootloader_type'), self.settings.get('bootloader_location'))
        else:
            logging.warning("Thus will not install any boot loader")

        if not self.testing:
            self.process = installation_process.InstallationProcess( \
                            self.settings, \
                            self.callback_queue, \
                            mount_devices, \
                            fs_devices, \
                            None, \
                            self.alternate_package_list)

            self.process.start()
        else:
            logging.warning(_("Testing mode. Thus won't apply any changes to your system!"))
        '''
