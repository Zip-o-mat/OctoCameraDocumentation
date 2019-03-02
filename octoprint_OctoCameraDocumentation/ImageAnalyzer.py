'''
Created on 22.02.2018
@author: Florens Wasserfall

'''

import re
import cv2
import numpy as np
from sklearn.svm import LinearSVC
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D


class ImageAnalyzer:

    gcode = None
    image = None
    pixel_per_mm_x = 10
    pixel_per_mm_y = 10
    offset_x = 0
    offset_y = 0

    def __init__(self, layerGCode, image, pixel_per_mm_x, pixel_per_mm_y, min_x, min_y, max_x, max_y):
        self.gcode = layerGCode
        self.image = image
        self.pixel_per_mm_x = pixel_per_mm_x
        self.pixel_per_mm_y = pixel_per_mm_y
        range_x = max_x - min_x
        range_y = max_y - min_y
        self.offset_x = min_x*pixel_per_mm_x - (image.shape[1] - range_x*pixel_per_mm_x) / 2
        self.offset_y = min_y*pixel_per_mm_y - (image.shape[0] - range_y*pixel_per_mm_y) / 2

    # extract all pixels which should have been generated by this extruder, based on
    # the gcode information. Returns a 1D list of BGR or HSV pixels
    def extruder_pixels(self, extruder_num, extrusion_width, HSV = False, limit = 0):
        mask = self.extruder_mask(extruder_num, extrusion_width)
        image = self.image.copy()
        if(HSV):
            print "converting to HSV!"
            image = cv2.cvtColor(self.image, cv2.COLOR_BGR2HSV)
        templated = cv2.bitwise_and(image, image, mask=mask)

        # extract list of pixels from masked image
        result = templated[np.any(templated != [0, 0, 0], axis=-1)]

        if(limit == 0):
            limit = len(result)
        return result[0:limit]


    # generate a mask image, covering all extrusion lines of given extruder for this layer
    def extruder_mask(self, extruder_num, extrusion_width):
        extrusion_width = int(extrusion_width * ((self.pixel_per_mm_x + self.pixel_per_mm_y) / 2))
        mask = np.zeros((self.image.shape[0], self.image.shape[1]), np.uint8)
        if(len(self.gcode[extruder_num]) > 1):
            for g in self.gcode[extruder_num]:
                cv2.line(mask, self._translate(g.a.x, g.a.y), self._translate(g.b.x, g.b.y), 255, extrusion_width, 8)
            #cv2.imshow("mask", mask)
            #cv2.waitKey(0)
        return mask


    # classifies all pixels in image by material of <extruder_num>.
    # Pixels belonging to this extruder are masked with <color>.
    # Internally uses a Linear SVM to classify pixels.
    def mark_extruder_pixels(self, extruder_num, max_extruder_num, extrusion_width, HSV = False, color = [0, 0, 255]):
        result = self.image.copy()
        hsv_image = self.image.copy()
        if(HSV):
            hsv_image = cv2.cvtColor(hsv_image, cv2.COLOR_BGR2HSV)

        pixels_extruder = self.extruder_pixels(extruder_num, extrusion_width, HSV=HSV, limit = 0)
        pixels_other = np.empty((0, 3), dtype=np.uint8)
        for i in range(max_extruder_num):
            if(i != extruder_num):
                pixels_other = np.vstack((pixels_other, self.extruder_pixels(i, 0.3, HSV=HSV, limit = 0)))

        # take random subsample
        svc_samples = 5000
        if(pixels_extruder.shape[0] > svc_samples):
            idx = np.random.randint(pixels_extruder.shape[0], size=svc_samples)
            pixels_extruder = pixels_extruder[idx,:]
        if(pixels_other.shape[0] > svc_samples):
            idx = np.random.randint(pixels_other.shape[0], size=svc_samples)
            pixels_other = pixels_other[idx,:]

        # plot color distribution
        fig = plt.figure(figsize=(15, 12))
        ax = fig.add_subplot(111, projection='3d')
        if(HSV):
            ax.set_xlabel("Hue")
            ax.set_ylabel("Saturation")
            ax.set_zlabel("Value")
        else:
            ax.set_xlabel("Blue")
            ax.set_ylabel("Green")
            ax.set_zlabel("Red")
        #ax.scatter(pixels_extruder[:, 0], pixels_extruder[:, 1], pixels_extruder[:, 2], c="red", s = 0.8)
        #ax.scatter(pixels_other[:, 0], pixels_other[:, 1], pixels_other[:, 2], c="black", s = 0.8)
        #plt.show()


        # prepare SVC data
        pixels = np.vstack((pixels_extruder, pixels_other))
        class_extruder = np.zeros(pixels_extruder.shape[0], dtype=int)
        #class_extruder = np.full((pixels_extruder.shape[0]), 'a')
        class_other = np.ones(pixels_other.shape[0], dtype=int)
        #class_other = np.full((pixels_extruder.shape[0]), 'b')
        classes = np.hstack((class_extruder, class_other))

        #train svm
        clf = LinearSVC(max_iter = 1000, dual = False, tol = 1e-4, verbose = 1)
        clf.fit(pixels, classes)

        # train svm again on the classified pixels to improve accuracy

        labels_extruder_2 = clf.predict(pixels_extruder)
        pixels_extruder_2 = pixels_extruder[np.equal(labels_extruder_2, 0)]

        labels_other_2 = clf.predict(pixels_other)
        pixels_other_2 = pixels_other[np.equal(labels_other_2, 1)]

        min_shape = min(pixels_extruder_2.shape[0], pixels_other_2.shape[0])
        pixels_extruder_2 = pixels_extruder_2[0:min_shape]
        pixels_other_2 = pixels_other_2[0:min_shape]

        # prepare SVC data
        pixels_2 = np.vstack((pixels_extruder_2, pixels_other_2))
        class_extruder_2 = np.zeros(pixels_extruder_2.shape[0], dtype=int)
        #class_extruder = np.full((pixels_extruder.shape[0]), 'a')
        class_other_2 = np.ones(pixels_other_2.shape[0], dtype=int)
        #class_other = np.full((pixels_extruder.shape[0]), 'b')
        classes_2 = np.hstack((class_extruder_2, class_other_2))

        #train svm again...
        clf = LinearSVC(max_iter = 1000, dual = False, tol = 1e-4, verbose = 1)
        clf.fit(pixels_2, classes_2)


        # classify all pixels in image
        #target_label = np.full((result.shape[1]), 'a', dtype=object)
        for row in range(result.shape[0]):
            row_labels = clf.predict(hsv_image[row,:,:])
            row_confidences = clf.decision_function(hsv_image[row,:,:])
            #print row_confidences
            #row_labels = clf.predict(result[row,:,:])
            #result[row,:,:][np.equal(row_labels, 0)] = [0, 0, 255]
            result[row,:,:][np.less(row_confidences, -2)] = [0, 0, 255] # accept only values with a certain distance to decision plane

        return result

    # traverses all extrusion lines for the given extruder number and tests for
    # the amount of <color> pixels. Returns False if an underfilled section was found
    # and an image with indication of the defect positions
    def traverse_gcode(self, masked_image, image, extruder_num, extrusion_width, marker_color = [0, 0, 255]):
        scaled_extrusion_width = int(extrusion_width * ((self.pixel_per_mm_x + self.pixel_per_mm_y) / 2))
        result_image = image.copy()
        result_bool = True
        if(len(self.gcode[extruder_num]) > 1):
            circle_mask = np.zeros((scaled_extrusion_width, scaled_extrusion_width), np.uint8)
            cv2.circle(circle_mask, (scaled_extrusion_width/2, scaled_extrusion_width/2), scaled_extrusion_width/2, 255, -1)
            circle_pixels = circle_mask[circle_mask == 255].shape[0]
            for g in self.gcode[extruder_num]:
                distance = 0
                while (distance < g.length()):
                    p = g.point_at(distance)
                    pos_x, pos_y = self._translate(p.x, p.y)
                    roi = masked_image[pos_y-scaled_extrusion_width/2:pos_y+scaled_extrusion_width/2, pos_x-scaled_extrusion_width/2:pos_x+scaled_extrusion_width/2]
                    overlay = cv2.bitwise_and(roi, roi, mask=circle_mask)
                    local_pixels = overlay[np.all(overlay == marker_color, axis=-1)]

                    if(local_pixels.shape[0]/float(circle_pixels) < 0.3):
                        cv2.circle(result_image, self._translate(p.x, p.y), scaled_extrusion_width/2, (0, 0, 255), -1)
                        result_bool = False

                    distance += extrusion_width/2

        return (result_bool, result_image)

    def _translate(self, x, y):
        return (int(x*self.pixel_per_mm_x - self.offset_x), 
            self.image.shape[0] - int(y*self.pixel_per_mm_y - self.offset_y))