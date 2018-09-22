# @author: niwics, niwi.cz, August 2018
# Simple single-purpose tool for merging two Strava workouts - first with HR, second without HR

import logging
from gpxpy import gpx
from gpxpy import parse as gpx_parse
from gpxpy import gpxfield as mod_gpxfield
from gpxpy.geo import length_3d
import datetime
import dateutil.parser
import re
from math import ceil
from copy import deepcopy
import os
import sys

from xml.etree import ElementTree

GARMIN_NS = 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1'
HR_BASE_TAG = '{{{}}}TrackPointExtension'.format(GARMIN_NS)
HR_TAG = '{{{}}}hr'.format(GARMIN_NS)
PACE_RE_PATTERN = r"(\d\d?):(\d\d)"

log = logging.getLogger(__name__)
ch = logging.StreamHandler()
log.addHandler(ch)

class StravaGpxException(Exception):
    pass

class StravaGpxTool:
    
    @staticmethod
    def compute_tracks_length(tracks):
        length = 0
        for track in tracks:
            for segment in track.segments:
                prev_point = None
                for point in segment.points:
                    if prev_point:
                        length += length_3d((prev_point, point))
                    prev_point = point
        return length

    def __init__(self, opts):
        self._opts = opts
        self._out_segment = None
    
    def addPoint(self, point):
        if not self._out_segment:
            raise StravaGpxException('Could not add point. No output segment defined.')
        self._out_segment.points.append(point)
    
    def process(self):
        
        # prepare the output
        out_gpx = gpx.GPX()
        out_track = gpx.GPXTrack()
        out_gpx.tracks.append(out_track)
        self._out_segment = gpx.GPXTrackSegment()
        out_track.segments.append(self._out_segment)

        if self._opts['mode'] == 'fill':
            self.fill()
        elif self._opts['mode'] == 'merge':
            self.merge()
        else:
            raise StravaGpxException('Invalid processing mode')
        
        # postprocessing - namespace
        out_gpx.nsmap['gpxtpx'] = GARMIN_NS
        
        try:
            with open(self._opts['output'], 'w') as stream:
                stream.write(out_gpx.to_xml())
        except IOError as e:
            raise StravaGpxException('Error while wtiting the output XML to file: {}'.
                format(self._opts['output']))
        log.info('Output GPX file written to "{}"'.format(self._opts['output']))
    
    def fill(self):
        in_file_name = self._opts['input']
        pace = self._opts.get('pace')
        start_time = end_time = duration_total = speed_ms = None
        hr = self._opts.get('hr')
        soft_mode = self._opts.get('soft')
        limit = self._opts.get('limit')

        # validations
        if not pace and not hr:
            raise StravaGpxException('ERROR: Nothing tho set (no HR or pace specified in program parameters)')
        if pace:
            pace_re = re.search(PACE_RE_PATTERN, pace)
            if not pace_re:
                raise StravaGpxException('Invalid format of pace: {}. Should be in format MM:SS.'.format(pace))
            if not self._opts['start_time'] or not self._opts['end_time']:
                raise StravaGpxException('ERROR: "start-time" and "end-time" arguments must be set for filling the pace.')
            # process start and end dates
            try:
                start_time = dateutil.parser.parse(self._opts.get('start_time'))
            except ValueError as e:
                raise StravaGpxException('Invalid "start_time" parameter: {}'.format(self._opts.get('start_time')))
            try:
                end_time = dateutil.parser.parse(self._opts.get('end_time'))
            except ValueError as e:
                raise StravaGpxException('Invalid "end_time" parameter: {}'.format(self._opts.get('end_time')))
            duration_total = (end_time - start_time).total_seconds()
            speed_ms = 1000.0 / (int(pace_re.group(1))*60+int(pace_re.group(2)))

        in_file = open(in_file_name, 'r')
        in_gpx = gpx_parse(in_file)

        total_length = 0
        total_length_current = 0
        pace_last_time = start_time
        moving_time = None

        if pace:
            total_length = StravaGpxTool.compute_tracks_length(in_gpx.tracks)
            moving_time = total_length / speed_ms
            pause_time = duration_total - moving_time

        log.info("Moving time: {}, speed: {}, total dist: {}".format(moving_time, speed_ms, total_length))

        i = 0
        for track in in_gpx.tracks:
            for segment in track.segments:
                prev_point = None
                length_from_prev = 0
                for point in segment.points:
                    duplicated_point = None
                    if prev_point:
                        length_from_prev = length_3d((prev_point, point))
                        total_length_current += length_from_prev
                    if hr:
                        if point.extensions and not soft_mode:
                            raise StravaGpxException('Existing *extension* value found.'+
                            'Consider running with --soft parameter. Value found: {}'.
                            format(point.extensions))
                        extension_element = ElementTree.Element(HR_BASE_TAG)
                        extension_element.text = ""
                        hr_element = ElementTree.Element(HR_TAG)
                        hr_element.text = str(hr)
                        extension_element.append(hr_element)
                        point.extensions.append(extension_element)
                    if pace:
                        log.debug("  DISTANCE: {}".format(length_from_prev))
                        if length_from_prev > 0:
                            # compute the arrival time to this point
                            next_time = pace_last_time + datetime.timedelta(
                                seconds = round(length_from_prev / speed_ms))
                            pace_last_time = min(next_time, end_time)
                            log.debug("Time moved to: {} (after move)".format(pace_last_time))
                            point.time = pace_last_time
                            # add the waiting time (proportionally from pause_time)
                            next_time = pace_last_time + datetime.timedelta(
                                seconds = round(pause_time * ((1.0*length_from_prev)/total_length)))
                            pace_last_time = min(next_time, end_time)
                            log.debug("Time moved to: {} (after pause)".format(pace_last_time))
                            duplicated_point = deepcopy(point)
                            duplicated_point.time = pace_last_time
                        else:
                            point.time = pace_last_time
                    self.addPoint(point)
                    if duplicated_point:
                        self.addPoint(duplicated_point)
                    prev_point = point
                    i += 1
                    if i == limit:
                        log.debug('Limit of processed trackpoint ({}) reached, ending.'.format(limit))
                        break
        log.info('Total distance: {}'.format(total_length_current))
    
    def merge(self):
        raise NotImplementedError('merge')

def main():

    # argparse example
    import argparse
    parser = argparse.ArgumentParser(description='Tool for manipulating GPX files for Strava.')
    parser.add_argument('--output', default='out.gpx', help="Output file file")
    parser.add_argument('--debug', action='store_true', help="Turn on debug mode")
    parser.add_argument('--limit', type=int, help="DEBUG argument: Limit the number of trackpoint to process")
    # program mode
    mode_subpars = parser.add_subparsers(dest='mode')
    mode_subpars.required = True
    merge_subpar = mode_subpars.add_parser('merge', help='Starts %(prog)s daemon')
    merge_subpar.add_argument('input1', help="Input file 1")
    merge_subpar.add_argument('input2', help="Input file 2")
    fill_subpars = mode_subpars.add_parser('fill', help='Fills GPX points with missing attributes: time or heart rate')
    fill_subpars.add_argument('input', help="Input file")
    fill_subpars.add_argument('--pace', help="Average pace to set for all points with this value missing. Format: MM:SS")
    fill_subpars.add_argument('--start-time', help="Start time for filling the pace (ISO format)")
    fill_subpars.add_argument('--end-time', help="ENd time for filling the pace (ISO format)")
    fill_subpars.add_argument('--hr', type=int, help="Heart rate to set for all points with this value missing")
    fill_subpars.add_argument('--soft', action='store_true', help="Soft mode - it fills just missig values and ignores existing")

    opts = vars(parser.parse_args())

    # logging level based on option
    log.setLevel("DEBUG" if opts['debug'] else "INFO")

    try:
        processor = StravaGpxTool(opts)
        processor.process()
    except StravaGpxException as e:
        log.error('Error while processing: {}'.format(e))
        sys.exit(os.EX_SOFTWARE)

if __name__ == "__main__":
    main()