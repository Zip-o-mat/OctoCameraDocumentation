# -*- coding: utf-8 -*-
"""
    This file is part of OctoCameraDocumenation base on FragJackers OctoCameraDocumentation

    OctoCameraDocumentation is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    OctoCameraDocumentation is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with OctoCameraDocumentation.  If not, see <http://www.gnu.org/licenses/>.

    Main author: Florens Wasserfall <wasserfall@informatik.uni-hamburg.de>
    Dennis Struhs <dennis.struhs@hamburg.de>
"""

from __future__ import absolute_import, division, print_function, unicode_literals


import octoprint.plugin
import flask
from subprocess import call
import os
import time
import datetime
import json
import cv2
import numpy as np
import regex as re

import time
import datetime

from collections import deque
from .GCodeProcessor import GCodeProcessor
from .GCodeProcessor import CustomJSONEncoder as CoordJSONify
from .GridGenerator import CameraGridMaker
from .ImageStitcher import ImageStitcher


__plugin_name__ = "OctoCameraDocumentation"
__plugin_version__ = "0.2"
__plugin_pythoncompat__ = ">=2.7,<4"

#instantiate plugin object and register hook for gcode injection
def __plugin_load__():

    octocameradocumentation = OctoCameraDocumentation()

    global __plugin_implementation__
    __plugin_implementation__ = octocameradocumentation

    global __plugin_hooks__
    __plugin_hooks__ = {'octoprint.comm.protocol.gcode.queuing': octocameradocumentation.hook_gcode_queuing}


class OctoCameraDocumentation(octoprint.plugin.StartupPlugin,
            octoprint.plugin.TemplatePlugin,
            octoprint.plugin.EventHandlerPlugin,
            octoprint.plugin.SettingsPlugin,
            octoprint.plugin.AssetPlugin,
            octoprint.plugin.SimpleApiPlugin,
            octoprint.plugin.BlueprintPlugin):

    def __init__(self):
        self.GCoordsList = []
        self.CameraGridCoordsList = []
        self.GridInfoList = []
        self.currentLayer = 0
        self.gridIndex = 0

        self.qeue = None

        self.CamPixelX = None
        self.CamPixelY = None

        self.our_pic_width = None
        self.our_pic_height = None

        self.currentPrintJobDir = None #Holds the current printjob folder dir

        self.mode = "normal" #Contains the mode for the camera callback

        self.currentTool = "T0" #saves the currently mounted tool
        self.lastTool = "T0" #Saves the last tool to change back after documentation

        self.image_array = [] #Stores the incoming images in an array
        self.MergedImage = None #Is created by stitching the tile images together

        self.repetition = 1 #Stores the current repetition
        self.elem = None #Stores the current element

    def on_after_startup(self):
        #used for communication to UI
        self._pluginManager = octoprint.plugin.plugin_manager()

        # Add helpers from the auxilary OctoPNP plug-in to grab images and camera resolution
        helpers = self._pluginManager.get_helpers("OctoPNP", "get_head_camera_image", "get_head_camera_pxPerMM")
        if helpers and "get_head_camera_image" in helpers:
            self._logger.info("FOUND HELPER FOR TAKING IMAGE!!!")
            self.get_camera_image = helpers["get_head_camera_image"]
        if helpers and "get_head_camera_pxPerMM" in helpers:
            self._logger.info("FOUND HELPER FOR CAMERARESOLUTION!!!")
            self.get_camera_resolution = helpers["get_head_camera_pxPerMM"]

    def get_settings_defaults(self):
        return{
            "active": True,
            "target_folder": "C:\Desktop",
            "picture_width": 800,
            "picture_height": 800,
            "overlap": 20,
            "stitching_active": True,
            "stitching_method": "Merge",
            "repetitions": 1,
            "extruders" : {
                "plastic": 0,
                "conductive": 1
            },
            "gcode" : {
                "prepic1": "",
                "postpic1": "",
                "prepic2": "",
                "postpic2": "",
                "prepic3": "",
                "postpic3": "",
                "prepic4": "",
                "postpic4": "",
                "prepic5": "",
                "postpic5": "",
            },
        }

    def get_template_configs(self):
        return [
            dict(type="tab", template="OctoCameraDocumentation_tab.jinja2"),
            dict(type="settings", template="OctoCameraDocumentation_settings.jinja2")
        ]

    def get_assets(self):
        return dict(
            js=["js/OctoCameraDocumentation.js",
                "js/camGrid.js",
                "js/settings.js"]
        )

    # GET endpoint, provides image resolution for the settings UI
    def on_api_get(self, request):
        self.mode = "resolution_get"

        # Get an image to determine the camera resolution
        self.get_camera_image(0, 0, "", "", self.get_camera_image_callback, True)

        self.our_pic_width = None
        self.our_pic_height = None
        # As long as the variables are not here, send python to sleep
        while(self.our_pic_width is None or self.our_pic_height is None):
            time.sleep(1)
        return flask.jsonify(width = self.our_pic_width,
                             height = self.our_pic_height)

    # Use the on_event hook to extract XML data every time a new file has been loaded by the user
    def on_event(self, event, payload):
        if(self._settings.get(["active"])):
            #extraxt part informations from inline xmly
            if event == "FileSelected":
                #Retrieve the basefolder for the GCode uploads
                dir_name = self._settings.global_get_basefolder("uploads")
                base_filename = payload.get("path")
                uploadsPath = os.path.join(dir_name, base_filename)
                file = self._openGCodeFiles(uploadsPath)

                # Extract layer-wise gcodes
                gcode_processor = GCodeProcessor(file, max(int(self._settings.get(["extruders", "plastic"])), int(self._settings.get(["extruders", "conductive"]))))
                self.GCoordsList = gcode_processor.gcodePerLayer()
                if not self.GCoordsList:
                    self._logger.error("GCode processing failed.")

                #Get the values for the Camera grid box sizes
                self._computeLookupGridValues()

                #Now create the actual grid
                self._createCameraGrid(
                    self.GCoordsList,
                    self.CamPixelX,
                    self.CamPixelY)

                self._logger.info("Created the camera lookup grid succesfully from the file: %s", payload.get("file"))
                self._logger.info( "Current Target folder setting is: %s", self._settings.get(["target_folder"]))
                self.image_array = []
                self.currentLayer = 0
                self._updateUI("FILE", "")

            # Create new Folder for dropping the images for the new printjob
            if(event == "PrintStarted"):
                self.currentPrintJobDir = self.getBasePath()
                os.mkdir(self.currentPrintJobDir)
                self.currentLayer = 0
                self.image_array = []

    def _createCameraGrid(self,inputList,CamResX,CamResY):
        templist = []
        infoList = []
        count = 0
        while count < len(inputList):
            #Creates a new CameraGridMaker Object with int Numbers for the Cam resolution
            grid_maker = CameraGridMaker(inputList,count,CamResX,CamResY)

            infoList.append([grid_maker.getMaxX(),
                    grid_maker.getMinX(),
                    grid_maker.getMaxY(),
                    grid_maker.getMinY(),
                    grid_maker.getCenterX(),
                    grid_maker.getCenterY(),
                    grid_maker.getGridRows(),
                    grid_maker.getGridCols()])
            templist.append(grid_maker.getCameraCoords())
            coords = ""
            for c in templist[-1]:
                coords += "[" + str(c.x) + "," + str(c.y) + "],"
            self._logger.info( "Camera coordinates for layer %d: %s", count, coords)
            count += 1

        #Retrieve the necessary variables to be forwarded to the Octoprint Canvas
        self.CameraGridCoordsList = templist
        self.GridInfoList = infoList


    """
    Use the gcode hook to start the camera grid documentation processes.
    """
    def hook_gcode_queuing(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if re.search('^T\d', cmd):
            self.currentTool = cmd

        if "M942" in cmd:
            if(self._settings.get(["active"])):
                print(self._settings.get(["repetitions"]))
                self._logger.info("Start Layer documentation with %d repetitions", self._settings.get_int(["repetitions"]))
                self.lastTool = self.currentTool 
                # Create the qeue for the printer camera coordinates
                self.qeue = deque(self.CameraGridCoordsList[self.currentLayer])
                self.elem = self.getNewQeueElem()
                self.repetition = 1
                if(self.elem):
                    # Pause the print to prevent interruptions from octoprint
                    if(self._printer.is_printing() or self._printer.is_resuming()):
                        self._printer.pause_print()
                    self._logger.info( "Qeued command to start the Camera documentation" )
                    self.get_camera_image(self.elem.x, self.elem.y, self._settings.get(["gcode", "prepic" + str(self.repetition)]), self._settings.get(["gcode", "postpic" + str(self.repetition)]), self.get_camera_image_callback, True)

        if "M945" in cmd:
            if(self._settings.get(["active"])):
                self.currentPrintJobDir = self.getBasePath()
                os.mkdir(self.currentPrintJobDir)
                self._logger.info( "Documentation Initialized in folder %s",  self.currentPrintJobDir)
                self.currentLayer = 0
                self.image_array = []


    def get_camera_image_callback(self, img):

        # Get the picture for the grid tiles here
        if(self.mode == "normal"):
            # Copy found files over to the target destination folder
            self.saveImageFiles(img)
            self._logger.info( "Saved image to: %s", self.getBasePath() )
            #
            if self._settings.get_int(["repetitions"]) > self.repetition:
                #next image with new repetition
                self.repetition += 1
            else:
                #set repetition back to 0 for next position
                self.repetition = 1
                # Get new element and continue taking pictures if qeue not empty
                self.elem = self.getNewQeueElem()
                        
            if(self.elem):
                self.get_camera_image(self.elem.x, self.elem.y, self._settings.get(["gcode", "prepic" + str(self.repetition)]), self._settings.get(["gcode", "postpic" + str(self.repetition)]), self.get_camera_image_callback, False)

        # Get the resolution for the settings button here
        if(self.mode == "resolution_get"):
            self.our_pic_width,self.our_pic_height = self._get_image_size(img)
            self._logger.info("The found image resolution was: %dx%d",self.our_pic_width,self.our_pic_height)
            self.mode = "normal" # Return to normal mode after finishing
        # else:
        #     return self._settings.get_int(["picture_width"]),
        #     self._settings.get_int(["picture_height"])

    def getNewQeueElem(self):
        if(self.qeue):
            self.gridIndex += 1 #Increment Tile after each deque
            return self.qeue.popleft()
        else:
            # Do image processing
            layer_config = self.GridInfoList[self.currentLayer]
            if len(self.image_array) > 0 and (self._settings.get(["stitching_active"])):
                image_stitcher = ImageStitcher(layer_config[6], layer_config[7], self._settings.get_int(["overlap"]), self.image_array)
                if self._settings.get(["stitching_method"]) == "TRIVIAL":
                    layer_image = image_stitcher.merge_trivial()
                if self._settings.get(["stitching_method"]) == "MERGE":
                    layer_image = image_stitcher.merge_stitching()
                cv2.imwrite(os.path.join(self.currentPrintJobDir, "layer"+str(self.currentLayer)+".png"), layer_image)
            self.image_array = []


            self.currentLayer += 1 #Increment layer when qeue was empty
            self.gridIndex = 0 #Reset Grid Index
            if(self._printer.is_paused() or self._printer.is_pausing()):
                self._logger.info( "Layer documentation finished, resuming printing." )
                self._printer.resume_print()
            self._printer.commands(self.lastTool)
            return(None)

    def saveImageFiles(self, img):
        # sometimes this function is called with an invalid image
        if type(img) is not np.ndarray: 
            self._logger.error("No valid image was handed to the plugin")
            return
        # save the image
        if self._settings.get_int(["repetitions"]) > 1:
            dest = os.path.join(self.currentPrintJobDir, 'Layer_{}'.format(self.currentLayer) + '_Tile_{}'.format(self.gridIndex) + '_{}'.format(self.repetition))
        else:
            dest = os.path.join(self.currentPrintJobDir, 'Layer_{}'.format(self.currentLayer) + '_Tile_{}'.format(self.gridIndex))
        
        # add the png ending if the image is a 16bit image
        if img.dtype == 'uint16':
            dest += '.png'
        else:
            dest += '.jpg'

        cv2.imwrite(dest, img)
        # and store into array for later processing
        self.image_array.append(img)

    def getBasePath(self):
        timestamp = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d__%H'+'h'+'_%M'+'m'+'_%S'+'s')
        return os.path.join(self._settings.get(["target_folder"]), 'Printjob_{}'.format(timestamp))

    def _openGCodeFiles(self, inputName):
        gcode = open( inputName, 'r' )
        readData = gcode.readlines()
        gcode.close()
        return readData

    def _computeLookupGridValues(self):
        PixelPerMillimeter = self.get_camera_resolution("HEAD")
        # Divide the resolution by the PixelPerMillimeter ratio
        self.CamPixelX = (self._settings.get_int(["picture_width"]) - 2*self._settings.get_int(["overlap"])) / PixelPerMillimeter['x']
        self.CamPixelY = (self._settings.get_int(["picture_height"]) - 2*self._settings.get_int(["overlap"])) / PixelPerMillimeter['y']

    """This function retrieves the resolution of the .png, .gif or .jpeg image file passed into it.
    This function was copypasted from https://stackoverflow.com/questions/8032642/how-to-obtain-image-size-using-standard-python-class-without-using-external-lib
    :param fname: Contains the filename of the file """
    def _get_image_size(self, img):
        if type(img) is np.ndarray:
            return img.shape[1], img.shape[0]
        else:
            return 0,0

    def _updateUI(self, event, parameter):
        data = dict(
            info="dummy"
        )
        if (event == "FILE"):
            if (self.GCoordsList != None):
                # compile part information
                data = dict(
                    gcodeCoordinates = json.dumps(self.GCoordsList,cls=CoordJSONify),
                    cameraCoordinates = json.dumps(self.CameraGridCoordsList,cls=CoordJSONify),
                    gridInfoList = json.dumps(self.GridInfoList,cls=CoordJSONify),
                    CamPixelResX = self.CamPixelX,
                    CamPixelResY = self.CamPixelY,
                )

        message = dict(
            event=event,
            data=data
        )
        self._pluginManager.send_plugin_message(__plugin_name__, message)
