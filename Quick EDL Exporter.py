# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

"""
Todo:
    Add importing capability
        Importing modes:
            append (default) - adds everything to the timeline, but keeps original data as well
            overwrite - deletes current timeline data
            update - only adds new files if they are not already in timeline, updates position of already-existing files if they are different
        Frame offset
        Channel offset

"""

import xml.etree.ElementTree as Tree
import bpy
import os
import math
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Vector, Color

bl_info = {
    "name": "Quick VSE Export And Import",
    "description": "Exports audio (and video) in the VSE timeline to EDL format to be used in Reaper, Samplitude and others.",
    "author": "Hudson Barkley (Snu/snuq/Aritodo)",
    "version": (0, 8, 2),
    "blender": (2, 80, 0),
    "location": "File > Export > Samplitude EDL (.edl); Vegas EDL (.txt)",
    "wiki_url": "",
    "tracker_url": "https://github.com/snuq/Quick-EDL-Exporter/issues",
    "category": "Import-Export"
}


def to_bool(value):
    """Function to convert various Non-Boolean true/false values to Boolean.
    Inputs that return True are:
        'Yes', 'yes', 'True', 'True', 'T', 't', '1', 1, 'Down', 'down'
    Any other value returns a False.
    """

    return str(value).lower() in ('yes', 'true', 't', '1', 'down')


def fades(sequence, direction):
    """Detects fadein and fadeout for sequences.
    This is a stripped down version of the function from VSE Quick Functions
    Arguments:
        sequence: VSE Sequence object that will be operated on
        direction: String, determines if the function works with fadein or fadeout
            in: fadein is operated on
            out: fadeout is operated on"""

    scene = bpy.context.scene

    #check if there is any animation data in the scene, just return 0 if there isnt
    if scene.animation_data is None:
        return 0
    if scene.animation_data.action is None:
        return 0

    all_curves = scene.animation_data.action.fcurves
    if direction == 'in':
        fade_low_point_frame = sequence.frame_final_start
    else:
        fade_low_point_frame = sequence.frame_final_end

    #set up the data value to fade
    if sequence.type == 'SOUND':
        fade_variable = 'volume'
    else:
        fade_variable = 'blend_alpha'

    #attempts to find the fade keyframes by iterating through all curves in scene
    fade_curve = False  #curve for the fades
    for curve in all_curves:
        if curve.data_path == 'sequence_editor.sequences_all["'+sequence.name+'"].'+fade_variable:
            #keyframes found
            fade_curve = curve
    if not fade_curve:
        #no fade animation curve found
        return 0

    #Detect fades
    fade_keyframes = fade_curve.keyframe_points
    if len(fade_keyframes) == 0:
        #no keyframes found
        return 0

    elif len(fade_keyframes) == 1:
        #only one keyframe
        return 0

    elif len(fade_keyframes) > 1:
        #at least 2 keyframes, there may be a fade
        if direction == 'in':
            fade_low_point = fade_keyframes[0]
            fade_high_point = fade_keyframes[1]
        elif direction == 'out':
            fade_low_point = fade_keyframes[-1]
            fade_high_point = fade_keyframes[-2]

        #check to see if the fade points are valid
        if fade_low_point.co[1] == 0:
            #opacity is 0, assume there is a fade
            if fade_low_point.co[0] == fade_low_point_frame:
                #fade low point is in the correct location
                if fade_high_point.co[1] > fade_low_point.co[1]:
                    #both fade points are valid
                    return abs(fade_high_point.co[0] - fade_low_point.co[0])
    return 0


def get_sample_rate():
    #Returns a string of the current sample rate in Blender's settings
    rate_text = bpy.context.preferences.system.audio_sample_rate
    if rate_text == 'RATE_48000':
        return '48000'
    elif rate_text == 'RATE_96000':
        return '96000'
    elif rate_text == 'RATE_192000':
        return '192000'
    else:
        return '44100'


def collect_files(limit_timeline=False, meta_sub=False, videos=''):
    #Returns a list of sequences that can be exported
    #limit_timeline will cause the function to ignore all sequences outside of the current timeline
    #meta_sub will cause the function to export ALL sequences, including those embedded in meta strips
    #videos can be 'NONE', 'SELECTED' or 'ALL', this will cause video-only files to be exported as well as audio

    scene = bpy.context.scene
    sequencer = scene.sequence_editor
    if meta_sub:
        sequences = sequencer.sequences_all
    else:
        sequences = sequencer.sequences
    export_sequences = []
    for sequence in sequences:
        if not limit_timeline or (sequence.frame_final_start <= scene.frame_end and sequence.frame_final_end > scene.frame_start):
            if sequence.type == 'SOUND':
                export_sequences.append(sequence)
            elif sequence.type == 'MOVIE':
                if videos == 'ALL' or (videos == 'SELECTED' and sequence.select):
                    found_audio = False
                    for seq in sequences:
                        if seq.type == 'SOUND' and seq.sound.filepath == sequence.filepath:
                            found_audio = True
                            break
                    if not found_audio:
                        export_sequences.append(sequence)
    return export_sequences


def get_tracks(sequences):
    maximum_channel = 0
    #determine number of channels
    for sequence in sequences:
        if sequence.channel > maximum_channel:
            maximum_channel = sequence.channel
    tracks = [list() for x in range(maximum_channel+1)]

    #sort files into channels
    index = 0
    for sequence in sequences:
        index = index + 1
        tracks[sequence.channel - 1].append([index, sequence])

    #simplify and sort channels
    sorted_tracks = []
    for track in tracks:
        if len(track) > 0:
            sorted_track = sorted(track, key=lambda x: x[1].frame_final_start)
            sorted_tracks.append(sorted_track)
    return sorted_tracks


def frames_to_seconds(frame):
    render = bpy.context.scene.render
    framerate = render.fps / render.fps_base
    seconds = frame / framerate
    return seconds


def frames_to_miliseconds(frame):
    return 1000 * frames_to_seconds(frame)


def get_volume(sequence):
    scene = bpy.context.scene
    volume = sequence.volume

    if scene.animation_data is not None:
        if scene.animation_data.action is not None:
            all_curves = scene.animation_data.action.fcurves

            #attempts to find the keyframes by iterating through all curves in scene
            fade_curve = False  #curve for the fades
            for curve in all_curves:
                if curve.data_path == 'sequence_editor.sequences_all["'+sequence.name+'"].volume':
                    #keyframes found
                    fade_curve = curve
                    break

            if fade_curve:
                fade_keyframes = fade_curve.keyframe_points
                if len(fade_keyframes) > 1:
                    #at least 2 keyframes, there may be a fade
                    fade_low_point = fade_keyframes[0]
                    fade_high_point = fade_keyframes[1]

                    #check to see if the fade points are valid
                    if (fade_low_point.co[1] == 0) and (fade_low_point.co[0] == sequence.frame_final_start) and (fade_high_point.co[1] > fade_low_point.co[1]):
                        #a valid fadein was found, return volume after fadein
                        return fade_high_point.co[1]
                    else:
                        #multiple keyframes, no fadein, find average volume
                        number_of_keyframes = len(fade_keyframes)
                        total_volume = 0
                        for keyframe in fade_keyframes:
                            if keyframe.co[0] == sequence.frame_final_end:
                                #ignore the last keyframe if at the end of the sequence, might be a fadeout
                                number_of_keyframes = number_of_keyframes - 1
                            else:
                                total_volume = total_volume + keyframe.co[1]
                        return total_volume / number_of_keyframes
    return volume


def get_fadein(sound):
    length = fades(sound, 'in')
    return length


def get_fadeout(sound):
    length = fades(sound, 'out')
    return length


def convert_to_db(volume):
    if volume > 0:
        volume_db = 20 * math.log(volume, 10)
        if volume_db > -150:
            return volume_db
    return -150


def export_vegas_edl(filename, limit_timeline=False, meta_sub=False, videos=''):
    sequences = collect_files(limit_timeline, meta_sub, videos)
    if os.path.isfile(filename):
        os.remove(filename)
    file = open(filename, "w")

    #write header
    file.write('"ID";"Track";"StartTime";"Length";"PlayRate";"Locked";"Normalized";"StretchMethod";"Looped";"OnRuler";"MediaType";"FileName";"Stream";"StreamStart";"StreamLength";"FadeTimeIn";"FadeTimeOut";"SustainGain";"CurveIn";"GainIn";"CurveOut";"GainOut";"Layer";"Color";"CurveInR";"CurveOutR":"PlayPitch";"LockPitch"\n')

    #write file info
    tracks = get_tracks(sequences)
    index = 0
    track_index = -1
    for track in tracks:
        track_index = track_index + 1
        for sequence_data in track:
            index = index + 1
            source, sequence = sequence_data
            start_time = frames_to_miliseconds(sequence.frame_final_start)
            length = frames_to_miliseconds(sequence.frame_final_duration)
            if sequence.type == 'SOUND':
                mediatype = 'AUDIO'
                filename = sequence.sound.filepath
            else:
                mediatype = 'VIDEO'
                filename = sequence.filepath
            stream_start = frames_to_miliseconds(sequence.frame_offset_start)
            fadein = get_fadein(sequence)
            fadein = frames_to_miliseconds(fadein)
            fadeout = get_fadeout(sequence)
            fadeout = frames_to_miliseconds(fadeout)
            if sequence.type == 'SOUND':
                volume = get_volume(sequence)
            else:
                volume = 1
            if sequence.lock:
                lk = 'TRUE'
            else:
                lk = 'FALSE'
            line = str(index)+';	'+str(track_index)+';	'+str(start_time)+';	'+str(length)+';	1.000000;	'+lk+';	FALSE;	0;	TRUE;	FALSE;	'+mediatype+';	"'+filename+'";	0;	'+str(stream_start)+';	'+str(length)+';	'+str(fadein)+';	'+str(fadeout)+';	'+str(volume)+';	2;	0.000000;	-2;	0.000000;	0;	-1;	0;	0;	0.000000;	FALSE;'
            file.write(line+'\n')
    file.close()


def get_sequences_data(sequences, for_export=True):
    sequences_data = []
    for index, sequence in enumerate(sequences):
        sequence_data = {
            'index': index,
            'modifiers': [],
            'tags': []
        }
        if sequence.type == "META":
            sequence_data['sequences'] = get_sequences_data(sequence.sequences, for_export=for_export)

        object_attributes = ['scene', 'scene_camera', 'mask', 'input_1', 'input_2', 'font']
        #For all types
        attributes = ['type', 'name', 'mute', 'lock', 'channel', 'frame_start', 'frame_final_start', 'frame_final_end', 'frame_still_start', 'frame_still_end']
        #For non-effect types
        attributes.extend(['animation_offset_start', 'animation_offset_end'])
        #For MOVIE, IMAGE, MOVIECLIP, MASK, SCENE, META, <effect> types
        attributes.extend(['alpha_mode', 'blend_alpha', 'blend_type', 'color_multiply', 'color_saturation', 'strobe', 'use_crop', 'use_deinterlace', 'use_flip_x', 'use_flip_y', 'use_float', 'use_reverse_frames', 'use_translation', 'use_proxy', 'use_linear_modifiers'])
        #For MOVIE type
        attributes.extend(['filepath', 'mpeg_preseek', 'stream_index'])
        #For IMAGE type
        attributes.extend(['directory'])
        #For MOVIECLIP type
        attributes.extend(['stabilize2d', 'undistort'])
        #Need to get the file for a movieclip somehow...
        #For SOUND type
        attributes.extend(['pan', 'pitch', 'show_waveform'])
        #For SCENE type
        attributes.extend(['use_grease_pencil', 'scene_input'])
        #For ALPHA_OVER, ALPHA_UNDER, CROSS, GAMMA_CROSS, OVER_DROP, WIPE types
        attributes.extend(['use_default_fade'])
        #For GAUSSIAN_BLUR type
        attributes.extend(['size_x', 'size_y'])
        #For WIPE type
        attributes.extend(['angle', 'blur_width', 'direction', 'transition_type'])
        #For GLOW type
        attributes.extend(['blur_radius', 'boost_factor', 'clamp', 'threshold', 'quality', 'use_only_boost'])
        #For TEXT type
        attributes.extend(['align_x', 'align_y', 'font_size', 'location', 'shadow_color', 'text', 'use_shadow', 'wrap_width'])
        #For TEXT, COLOR types
        attributes.extend(['color'])
        #For TRANSFORM type
        attributes.extend(['interpolation', 'rotation_start', 'scale_start_x', 'scale_start_y', 'translate_start_x', 'translate_start_y', 'translation_unit', 'use_uniform_scale'])
        #For SPEED type
        attributes.extend(['multiply_speed', 'speed_factor', 'use_as_speed'])
        #For MULTICAM type
        attributes.extend(['multicam_source'])
        #VSEQF Variables
        attributes.extend(['parent'])

        #Get basic attributes
        for attribute in attributes:
            if hasattr(sequence, attribute):
                attr = getattr(sequence, attribute)
                sequence_data[attribute] = attr

        #Get object attributes
        for attribute in object_attributes:
            if hasattr(sequence, attribute):
                attr = getattr(sequence, attribute)
                if for_export:
                    if attr is not None:
                        attr = attr.name
                    else:
                        attr = ''
                sequence_data[attribute] = attr

        #Get group attributes
        if hasattr(sequence, 'colorspace_settings'):
            if sequence.colorspace_settings:
                sequence_data['colorspace_settings'] = sequence.colorspace_settings.name
            else:
                sequence.colorspace_settings = ''
        if hasattr(sequence, 'crop'):
            if sequence.crop:
                sequence_data['crop_max_x'] = sequence.crop.max_x
                sequence_data['crop_max_y'] = sequence.crop.max_y
                sequence_data['crop_min_x'] = sequence.crop.min_x
                sequence_data['crop_min_y'] = sequence.crop.min_y
        if hasattr(sequence, 'transform'):
            if sequence.transform:
                sequence_data['transform_offset_x'] = sequence.transform.offset_x
                sequence_data['transform_offset_y'] = sequence.transform.offset_y
        if hasattr(sequence, 'proxy'):
            if sequence.proxy:
                sequence_data['proxy_build_100'] = sequence.proxy.build_100
                sequence_data['proxy_build_75'] = sequence.proxy.build_75
                sequence_data['proxy_build_50'] = sequence.proxy.build_50
                sequence_data['proxy_build_25'] = sequence.proxy.build_25
                sequence_data['proxy_directory'] = sequence.proxy.directory
                sequence_data['proxy_filepath'] = sequence.proxy.filepath
                sequence_data['proxy_quality'] = sequence.proxy.quality
                sequence_data['proxy_use_overwrite'] = sequence.proxy.use_overwrite
                sequence_data['proxy_use_proxy_custom_directory'] = sequence.proxy.use_proxy_custom_directory
                sequence_data['proxy_use_proxy_custom_file'] = sequence.proxy.use_proxy_custom_file
        if hasattr(sequence, 'sound'):
            if sequence.sound:
                sequence_data['sound_filepath'] = sequence.sound.filepath
                sequence_data['use_mono'] = sequence.sound.use_mono
        if hasattr(sequence, 'elements'):
            if sequence.elements:
                elements = []
                for element in sequence.elements:
                    elements.append(element.filename)
                sequence_data['elements'] = elements

        #Modifiers
        if hasattr(sequence, 'modifiers'):
            for modifier in sequence.modifiers:
                #Standard attributes
                modifier_attributes = ['type', 'name', 'mute', 'mask_time', 'input_mask_type']
                #Individual attributes
                modifier_attributes.extend(['color_multiply', 'bright', 'contrast', 'white_value', 'adaptation', 'contrast', 'correction', 'gamma', 'intensity', 'key', 'offset', 'tonemap_type'])

                modifier_data = {}
                attr = getattr(modifier, 'input_mask_id')
                if for_export:
                    if attr is not None:
                        attr = attr.name
                    else:
                        attr = ''
                modifier_data['input_mask_id'] = attr
                attr = getattr(modifier, 'input_mask_strip')
                if for_export:
                    if attr is not None:
                        attr = attr.name
                    else:
                        attr = ''
                modifier_data['input_mask_strip'] = attr

                #For COLOR_BALANCE
                if hasattr(modifier, 'color_balance'):
                    modifier_data['color_balance_gain'] = modifier.color_balance.gain
                    modifier_data['color_balance_gamma'] = modifier.color_balance.gamma
                    modifier_data['color_balance_invert_gain'] = modifier.color_balance.invert_gain
                    modifier_data['color_balance_invert_gamma'] = modifier.color_balance.invert_gamma
                    modifier_data['color_balance_invert_lift'] = modifier.color_balance.invert_lift
                    modifier_data['color_balance_lift'] = modifier.color_balance.lift
                if hasattr(modifier, 'curve_mapping'):
                    modifier_data['curve_mapping_black_level'] = modifier.curve_mapping.black_level
                    modifier_data['curve_mapping_clip_max_x'] = modifier.curve_mapping.clip_max_x
                    modifier_data['curve_mapping_clip_max_y'] = modifier.curve_mapping.clip_max_y
                    modifier_data['curve_mapping_clip_min_x'] = modifier.curve_mapping.clip_min_x
                    modifier_data['curve_mapping_clip_min_y'] = modifier.curve_mapping.clip_min_y
                    modifier_data['curve_mapping_use_clip'] = modifier.curve_mapping.use_clip
                    modifier_data['curve_mapping_white_level'] = modifier.curve_mapping.white_level
                    modifier_data['curves'] = []

                    for curve in modifier.curve_mapping.curves:
                        curve_data = {}
                        curve_data['extend'] = curve.extend
                        curve_data['points'] = []
                        for point in curve.points:
                            point_data = {
                                'handle_type': point.handle_type,
                                'location': point.location
                            }
                            curve_data['points'].append(point_data)
                        modifier_data['curves'].append(curve_data)
                sequence_data['modifiers'].append(modifier_data)

        if hasattr(sequence, 'tags'):
            for tag in sequence.tags:
                tag_data = {
                    'text': tag.text,
                    'use_offset': tag.use_offset,
                    'offset': tag.offset,
                    'length': tag.length,
                    'color': tag.color
                }
                sequence_data['tags'].append(tag_data)

        sequences_data.append(sequence_data)
    return sequences_data


def get_timeline_data(scene, for_export=True):
    #returns a complete set of relevant timeline data for the given scene
    #'for_export' command will replace all object data with the object's name

    render = scene.render
    markers = scene.timeline_markers
    sequences = bpy.context.sequences
    timeline_data = {
        'name': scene.name,
        'frame_start': scene.frame_start,
        'frame_end': scene.frame_end,
        'fps': render.fps,
        'fps_base': render.fps_base,
        'resolution_x': render.resolution_x,
        'resolution_y': render.resolution_y,
        'pixel_aspect_x': render.pixel_aspect_x,
        'pixel_aspect_y': render.pixel_aspect_y,
        'markers': [],
        'sequences': [],
        'animations': []
    }
    for marker in markers:
        marker_data = {
            'name': marker.name,
            'frame': marker.frame
        }
        timeline_data['markers'].append(marker_data)

    timeline_data['sequences'] = get_sequences_data(sequences, for_export=for_export)

    #Animations
    if scene.animation_data:
        if scene.animation_data.action:
            for fcurve in scene.animation_data.action.fcurves:
                if fcurve.data_path.startswith('sequence_editor.sequences_all['):
                    fcurve_data = {
                        'array_index': fcurve.array_index,
                        'data_path': fcurve.data_path,
                        'extrapolation': fcurve.extrapolation,
                        'keyframe_points': [],
                        'modifiers': []
                    }
                    for keyframe_point in fcurve.keyframe_points:
                        keyframe_data = {
                            'co': keyframe_point.co,
                            'handle_left': keyframe_point.handle_left,
                            'handle_left_type': keyframe_point.handle_left_type,
                            'handle_right': keyframe_point.handle_right,
                            'handle_right_type': keyframe_point.handle_right_type,
                            'interpolation': keyframe_point.interpolation
                        }
                        fcurve_data['keyframe_points'].append(keyframe_data)
                    for modifier in fcurve.modifiers:
                        modifier_attributes = ['type', 'blend_in', 'blend_out', 'frame_end', 'frame_start', 'influence', 'mute', 'use_additive', 'use_influence', 'use_restricted_range']
                        #Generator
                        modifier_attributes.extend(['mode', 'poly_order'])
                        #.coefficients
                        #Built-In Function
                        modifier_attributes.extend(['amplitude', 'function_type', 'phase_multiplier', 'phase_offset', 'value_offset'])
                        #Envelope
                        modifier_attributes.extend(['default_max', 'default_min', 'reference_value'])
                        #.control_points
                        #Cycles
                        modifier_attributes.extend(['cycles_before', 'cycles_after', 'mode_before', 'mode_after'])
                        #Noise
                        modifier_attributes.extend(['blend_type', 'depth', 'offset', 'phase', 'scale', 'strength'])
                        #Limits
                        modifier_attributes.extend(['max_x', 'max_y', 'min_x', 'min_y', 'use_max_x', 'use_max_y', 'use_min_x', 'use_min_y'])
                        #Stepped
                        modifier_attributes.extend(['frame_offset', 'frame_step', 'use_frame_start', 'use_frame_end'])

                        modifier_data = {}
                        for attribute in modifier_attributes:
                            if hasattr(modifier, attribute):
                                attr = getattr(modifier, attribute)
                                modifier_data[attribute] = attr
                        if hasattr(modifier, 'coefficients'):
                            coefficients = []
                            for coefficient in modifier.coefficients:
                                coefficients.append(coefficient)
                            modifier_data['coefficients'] = coefficients
                        if hasattr(modifier, 'control_points'):
                            control_points = []
                            for control_point in modifier.control_points:
                                control_points.append(control_point)
                            modifier_data['control_points'] = control_points

                        fcurve_data['modifiers'].append(modifier_data)
                    timeline_data['animations'].append(fcurve_data)
    return timeline_data


def export_timeline_data(filename):
    scene = bpy.context.scene
    timeline_data = get_timeline_data(scene, for_export=True)
    root = Tree.Element(scene.name)
    buildxml(root, timeline_data)
    indent(root)
    xml_data = Tree.tostring(root, encoding='unicode')
    xml_file = open(filename, 'w')
    xml_file.write(xml_data)
    xml_file.close()


def islistsubclass(data):
    if isinstance(data, tuple) or isinstance(data, list):
        return True
    if isinstance(data, Vector):
        return True
    if isinstance(data, Color):
        return True

    return False


def buildxml(root, data):
    if isinstance(data, dict):
        for key, value in data.items():
            subelement = Tree.SubElement(root, key)
            buildxml(subelement, value)
    elif islistsubclass(data):
        for value in data:
            subelement = Tree.SubElement(root, 'i')
            buildxml(subelement, value)
    else:
        root.text = str(data)
    return root


def builddata(root):
    if list(root):
        #root is a dictionary item, parse individual elements
        data = {}
        for element in root:
            children = list(element)
            if children:
                #element is a list
                data[element.tag] = []
                for child in children:
                    child_data = builddata(child)
                    data[element.tag].append(child_data)
            else:
                #element is just a dictionary component
                data[element.tag] = element.text
    else:
        #root is a single point of data
        return root.text
    return data


def indent(elem, level=0):
    i = "\n" + level*"  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level+1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def import_timeline_data(data, mode='APPEND', offset_x=0, offset_y=0):
    #Function that puts a timeline_data element into an actual timeline

    report = []
    if mode == 'NEW':
        #Make a new scene and import timeline to it
        bpy.ops.scene.new(type='EMPTY')
    scene = bpy.context.scene
    if mode == 'REPLACE':
        #Remove all sequences from current timeline
        for sequence in reversed(scene.sequences):
            scene.sequences.remove(sequence)
    set_props(scene, ['name', 'frame_start', 'frame_end'], data)
    render = scene.render
    set_props(render, ['fps', 'fps_base', 'resolution_x', 'resolution_y', 'pixel_aspect_x', 'pixel_aspect_y'], data)

    for index, marker_data in enumerate(data['markers']):
        try:
            marker_frame = int(marker_data['frame'])
            marker_name = marker_data['name']
        except:
            report.append("Unable to add marker "+str(index))
            continue
        marker_exists = False
        for marker in scene.timeline_markers:
            if marker.frame == marker_frame:
                marker.name = marker_name
                marker_exists = True
                break
        if not marker_exists:
            scene.timeline_markers.new(name=marker_name, frame=marker_frame)
        #report.append('Added marker '+marker_name)

    sequencer = scene.sequence_editor
    sequences = sequencer.sequences
    added = add_sequences(data['sequences'], sequences, sequencer, report, offset_x, offset_y)

    for animation_data in data['animations']:
        #Todo
        pass


def add_sequences(sequence_datas, sequences, sequencer, report, offset_x, offset_y):
    #Todo: sort sequences, ensure effects are added after the strips that they will be applied to
    added_sequences = []
    for index, sequence_data in enumerate(sequence_datas):
        added = add_sequence(sequence_data, sequences, sequencer, report, offset_x, offset_y)
        if added is not None:
            added_sequences.append(added)
    return added_sequences


def add_sequence(sequence_data, sequences, sequencer, report, offset_x, offset_y):
    #Create sequence
    try:
        seq_name = sequence_data['name']
    except:
        message = "Unable to load sequence"
        report.append(message)
        return None
    try:
        seq_type = sequence_data['type']
        seq_channel = int(sequence_data['channel'])
        seq_frame_start = int(sequence_data['frame_start'])

    except:
        #not able to load essential data
        message = "Unable to load sequence '"+seq_name+"'"
        report.append(message)
        return None

    if seq_type == 'META':
        if 'sequences' in sequence_data:
            bpy.ops.sequencer.select_all(action='DESELECT')
            to_meta = add_sequences(sequence_data['sequences'], sequences, sequencer, report, offset_x, offset_y)

            for seq in to_meta:
                seq.select = True

            bpy.ops.sequencer.meta_make()
            new_sequence = sequencer.active_strip
            new_sequence.name = seq_name
            new_sequence.frame_start = seq_frame_start
            set_sequence_position(new_sequence, sequence_data, offset_x, offset_y)

    elif seq_type == 'MOVIE':
        try:
            seq_filepath = sequence_data['filepath']
        except:
            #not able to load essential data
            message = "Unable to load sequence '"+seq_name+"'"
            report.append(message)
            return None
        new_sequence = sequences.new_movie(name=seq_name, filepath=seq_filepath, channel=seq_channel, frame_start=seq_frame_start)
        set_sequence_position(new_sequence, sequence_data, offset_x, offset_y)
        set_props(new_sequence, ['mpeg_preseek', 'stream_index'], sequence_data)

    elif seq_type == 'SOUND':
        try:
            sound_filepath = sequence_data['sound_filepath']
        except:
            sound_filepath = ''
        new_sequence = sequences.new_sound(name=seq_name, filepath=sound_filepath, channel=seq_channel, frame_start=seq_frame_start)
        set_sequence_position(new_sequence, sequence_data, offset_x, offset_y)
        set_prop(new_sequence.sound, 'use_mono', sequence_data)
        set_props(new_sequence, ['pan', 'pitch', 'show_waveform'], sequence_data)

    elif seq_type == 'IMAGE':
        try:
            image_directory = sequence_data['directory']
            image_elements = sequence_data['elements']
            image_filepath = os.path.join(image_directory, image_elements[0])
            elements = image_elements[1:]
            new_sequence = sequences.new_image(name=seq_name, filepath=image_filepath, channel=seq_channel, frame_start=seq_frame_start)
            for element in elements:
                new_sequence.elements.append(element)
            set_sequence_position(new_sequence, sequence_data, offset_x, offset_y)
        except:
            report.append("Unable to find source files for sequence '"+seq_name+"'")
            return None

    elif seq_type == 'MOVIECLIP':
        #todo: new_clip()
        #Cant import clips right now, vse missing functionality there
        report.append("Unable to import Movie Clip sequence, missing functionality in VSE")
        return None

    elif seq_type == 'MASK':
        try:
            mask_name = sequence_data['mask']
        except:
            mask_name = ''
        if mask_name in bpy.data.masks:
            seq_mask = bpy.data.masks[mask_name]
        else:
            #Mask not found, create a new one
            report.append("Mask '"+mask_name+"' not found, created a new mask.")
            seq_mask = bpy.data.masks.new(name=mask_name)
        new_sequence = sequences.new_mask(name=seq_name, mask=seq_mask, channel=seq_channel, frame_start=seq_frame_start)
        set_sequence_position(new_sequence, sequence_data, offset_x, offset_y)

    elif seq_type == 'SCENE':
        try:
            scene_name = sequence_data['scene']
        except:
            scene_name = ''
        if scene_name in bpy.data.scenes:
            seq_scene = bpy.data.scenes[scene_name]
        else:
            #Scene not found, create a new one
            report.append("Scene '"+scene_name+"' not found, created a new scene.")
            scene = bpy.context.scene
            bpy.ops.scene.new(type='EMPTY')
            seq_scene = bpy.context.scene
            bpy.context.window.scene = scene

        new_sequence = sequences.new_scene(name=seq_name, scene=seq_scene, channel=seq_channel, frame_start=seq_frame_start)
        set_sequence_position(new_sequence, sequence_data, offset_x, offset_y)
        try:
            camera_name = sequence_data['scene_camera']
        except:
            camera_name = ''
        if camera_name in seq_scene.objects:
            scene_camera = seq_scene.objects[camera_name]
            new_sequence.scene_camera = scene_camera
        set_props(new_sequence, ['use_grease_pencil', 'scene_input'], sequence_data)

    else:
        #Effects
        #need to figure out input_1 and input_2
        new_sequence = sequencer.new_effect(name=seq_name, type=seq_type, channel=seq_channel, frame_start=seq_frame_start)
        #Todo


        try:
            font_name = sequence_data['font']
            if font_name in bpy.data.fonts:
                seq_font = bpy.data.fonts[font_name]
                new_sequence.font = seq_font
        except:
            pass

        #For ALPHA_OVER, ALPHA_UNDER, CROSS, GAMMA_CROSS, OVER_DROP, WIPE types
        attributes = ['use_default_fade']
        #For GAUSSIAN_BLUR type
        attributes.extend(['size_x', 'size_y'])
        #For WIPE type
        attributes.extend(['angle', 'blur_width', 'direction', 'transition_type'])
        #For GLOW type
        attributes.extend(['blur_radius', 'boost_factor', 'clamp', 'threshold', 'quality', 'use_only_boost'])
        #For TEXT type
        attributes.extend(['align_x', 'align_y', 'font_size', 'location', 'shadow_color', 'text', 'use_shadow', 'wrap_width'])
        #For TEXT, COLOR types
        attributes.extend(['color'])
        #For TRANSFORM type
        attributes.extend(['interpolation', 'rotation_start', 'scale_start_x', 'scale_start_y', 'translate_start_x', 'translate_start_y', 'translation_unit', 'use_uniform_scale'])
        #For SPEED type
        attributes.extend(['multiply_speed', 'speed_factor', 'use_as_speed'])
        #For MULTICAM type
        attributes.extend(['multicam_source'])
        set_props(new_sequence, attributes, sequence_data)

    #Set general attributes
    attributes = ['mute', 'lock', 'alpha_mode', 'blend_alpha', 'blend_type', 'color_multiply', 'color_saturation', 'strobe', 'use_crop', 'use_deinterlace', 'use_flip_x', 'use_flip_y', 'use_float', 'use_reverse_frames', 'use_translation', 'use_proxy', 'use_linear_modifiers', 'stabilize2d', 'undistort']
    #VSEQF Variables
    attributes.extend(['parent'])
    set_props(new_sequence, attributes, sequence_data)

    set_prop(new_sequence.colorspace_settings, 'name', sequence_data, data_prop='colorspace_settings')

    if hasattr(new_sequence, 'crop'):
        if new_sequence.crop:
            set_prop(new_sequence.crop, 'max_x', sequence_data, data_prop='crop_max_x')
            set_prop(new_sequence.crop, 'max_y', sequence_data, data_prop='crop_max_y')
            set_prop(new_sequence.crop, 'min_x', sequence_data, data_prop='crop_min_x')
            set_prop(new_sequence.crop, 'min_y', sequence_data, data_prop='crop_min_y')
    if hasattr(new_sequence, 'transform'):
        if new_sequence.transform:
            set_prop(new_sequence.transform, 'offset_x', sequence_data, data_prop='transform_offset_x')
            set_prop(new_sequence.transform, 'offset_y', sequence_data, data_prop='transform_offset_y')
    if hasattr(new_sequence, 'proxy'):
        if new_sequence.proxy:
            set_prop(new_sequence.proxy, 'build_100', sequence_data, data_prop='proxy_build_100')
            set_prop(new_sequence.proxy, 'build_75', sequence_data, data_prop='proxy_build_75')
            set_prop(new_sequence.proxy, 'build_50', sequence_data, data_prop='proxy_build_50')
            set_prop(new_sequence.proxy, 'build_25', sequence_data, data_prop='proxy_build_25')
            set_prop(new_sequence.proxy, 'directory', sequence_data, data_prop='proxy_directory')
            set_prop(new_sequence.proxy, 'filepath', sequence_data, data_prop='proxy_filepath')
            set_prop(new_sequence.proxy, 'quality', sequence_data, data_prop='proxy_quality')
            set_prop(new_sequence.proxy, 'use_overwrite', sequence_data, data_prop='proxy_use_overwrite')
            set_prop(new_sequence.proxy, 'use_proxy_custom_directory', sequence_data, data_prop='proxy_use_proxy_custom_directory')
            set_prop(new_sequence.proxy, 'use_proxy_custom_file', sequence_data, data_prop='proxy_use_proxy_custom_file')

    try:
        #Todo: need to add modifiers on a second pass since mask inputs can be strips
        modifiers = sequence_data['modifiers']
        for modifier_data in modifiers:
            try:
                modifier_type = modifier_data['type']
                modifier_name = modifier_data['name']
                modifier = new_sequence.modifiers.new(name=modifier_name, type=modifier_type)
                modifier_attributes = ['mute', 'mask_time', 'input_mask_type']

                modifier_attributes.extend(['color_multiply', 'bright', 'contrast', 'white_value', 'adaptation', 'contrast', 'correction', 'gamma', 'intensity', 'key', 'offset', 'tonemap_type'])

                set_props(modifier, modifier_attributes, modifier_data)
            except:
                pass
    except:
        pass

    try:
        #Load tags
        tags = sequence_data['tags']
        for tag_data in tags:
            tag = new_sequence.tags.add()
            set_props(tag, ['text', 'use_offset', 'offset', 'length'], tag_data)
            tag_color = tag_data['color']
            tag.color = tag_color
    except:
        pass

    return new_sequence


def set_sequence_position(sequence, data, offset_x=0, offset_y=0):
    set_prop(sequence, 'frame_final_start', data, offset=offset_x)
    set_prop(sequence, 'frame_final_end', data, offset=offset_x)
    set_prop(sequence, 'frame_still_start', data, offset=offset_x)
    set_prop(sequence, 'frame_still_end', data, offset=offset_x)
    set_prop(sequence, 'animation_offset_start', data, offset=offset_x)
    set_prop(sequence, 'animation_offset_end', data, offset=offset_x)
    set_prop(sequence, 'channel', data, offset=offset_y)
    set_prop(sequence, 'frame_start', data, offset=offset_x)


def set_props(set_to, props, data):
    for prop in props:
        set_prop(set_to, prop, data)


def set_prop(set_to, prop, data, data_prop=None, offset=0):
    if not hasattr(set_to, prop):
        return False
    if data_prop is None:
        data_prop = prop
    if prop in data:
        variable = data[data_prop]
        if isinstance(getattr(set_to, prop), float):
            try:
                variable = float(variable) + offset
            except:
                variable = 0.0 + offset
        elif isinstance(getattr(set_to, prop), bool):
            variable = to_bool(variable)
        elif isinstance(getattr(set_to, prop), int):
            try:
                variable = int(variable) + offset
            except:
                variable = 0 + offset
        setattr(set_to, prop, variable)
        return True
    return False


def export_samplitude_edl(filename, limit_timeline=False, meta_sub=False, videos=''):
    title = 'Blender EDL Export'
    samplerate = get_sample_rate()
    samples = int(samplerate)
    channels = 2
    sequences = collect_files(limit_timeline, meta_sub, videos)
    if os.path.isfile(filename):
        os.remove(filename)
    file = open(filename, "w")

    #write header
    file.write("Samplitude EDL File Format Version 1.5\n")
    file.write('Title: "'+title+'"\n')
    file.write('Sample Rate: '+samplerate+'\n')
    file.write('Output Channels: '+str(channels)+'\n')
    file.write('\n\n')

    #write audio file info
    file.write('Source Table Entries: '+str(len(sequences))+'\n')
    index = 0
    for sequence in sequences:
        index = index + 1
        if sequence.type == 'SOUND':
            filename = sequence.sound.filepath
        else:
            filename = sequence.filepath
        file.write('   '+str(index)+' "'+filename+'"\n')
    file.write('\n')

    #write tracks
    tracks = get_tracks(sequences)
    index = 0
    for track in tracks:
        index = index + 1
        track_name = 'Track '+str(index)
        file.write('Track '+str(index)+': "'+track_name+'" Solo: 0 Mute: 0\n')
        for sequence_data in track:
            source, sequence = sequence_data
            track = str(index)
            #note: sound position is based on seconds*samples
            #record-in == offset_start
            play_in = int(round(samples * frames_to_seconds(sequence.frame_final_start)))
            play_out = int(round(samples * frames_to_seconds(sequence.frame_final_end)))
            record_in = int(round(samples * frames_to_seconds(sequence.frame_offset_start)))
            record_out = int(round(samples * frames_to_seconds(sequence.frame_offset_end)))
            if sequence.type == 'SOUND':
                volume = get_volume(sequence)
            else:
                volume = 1
            volume = convert_to_db(volume)
            if sequence.mute:
                mt = '1'
            else:
                mt = '0'
            if sequence.lock:
                lk = '1'
            else:
                lk = '0'
            fadein = get_fadein(sequence)
            fadein_percent = 0
            fadein = int(round(samples * frames_to_seconds(fadein)))
            fadein_curve = '"*default"'
            fadeout = get_fadeout(sequence)
            fadeout_percent = 0
            fadeout = int(round(samples * frames_to_seconds(fadeout)))
            fadeout_curve = '"*default"'
            name = sequence.name
            line = str(source)+'  '+track+'  '+str(play_in)+'  '+str(play_out)+'  '+str(record_in)+'  '+str(record_out)+'  '+str(volume)+'  '+mt+'  '+lk+'  '+str(fadein)+'  '+str(fadein_percent)+'  '+fadein_curve+'  '+str(fadeout)+'  '+str(fadeout_percent)+'  '+fadeout_curve+'  "'+name+'"'
            file.write(line+'\n')
        file.write('\n')
    file.write('\n')
    index = 0

    #unfortunately, Blender has no track specific settings, so just add placeholders for that stuff
    for track in tracks:
        index = index + 1
        file.write('Volume for Track '+str(index)+':\n')
        file.write('   0 0.000\n')
        file.write('\n')
        file.write('Pan for Track '+str(index)+':\n')
        file.write('   0 1.00000\n')
        file.write('\n')
    file.close()


class VegasEDLExport(bpy.types.Operator, ExportHelper):
    bl_idname = "sequencer.vegas_export"
    bl_label = "Export Vegas EDL (.txt)"

    filepath: bpy.props.StringProperty()
    filename_ext = ".txt"
    filter_glob: bpy.props.StringProperty(default="*.txt", options={'HIDDEN'})
    check_extension = True

    only_current_timeline: bpy.props.BoolProperty(
        name='Export Only Current Timeline',
        description='Ignore sequences outside of the current set timeline.',
        default=False)
    export_meta_subsequences: bpy.props.BoolProperty(
        name='Export From Inside MetaStrips',
        description='Drill into and export all files from inside meta strips as well as the current timeline.',
        default=False)
    videos: bpy.props.EnumProperty(
        name="Videos",
        items=(('NONE', 'Only Export Audio', ""), ("SELECTED", "Export Selected Videos", ""), ("ALL", "Export All Videos", "")),
        default='NONE')

    def execute(self, context):
        if self.filepath:
            if not self.filepath.lower().endswith('.txt'):
                self.filepath = self.filepath + '.txt'
            try:
                export_vegas_edl(self.filepath, limit_timeline=self.only_current_timeline, meta_sub=self.export_meta_subsequences, videos=self.videos)
                self.report({'INFO'}, "Saved file to: "+self.filepath)
            except Exception as e:
                self.report({'WARNING'}, 'Could not save file: '+self.filepath+', '+str(e))
        else:
            self.report({'WARNING'}, 'No file to export to')
        return{'FINISHED'}


class SamplitudeEDLExport(bpy.types.Operator, ExportHelper):
    bl_idname = "sequencer.samplitude_export"
    bl_label = "Export Samplitude EDL (.edl)"

    filepath: bpy.props.StringProperty()
    filename_ext = ".edl"
    filter_glob: bpy.props.StringProperty(default="*.edl", options={'HIDDEN'})
    check_extension = True

    only_current_timeline: bpy.props.BoolProperty(
        name='Export Only Current Timeline',
        description='Ignore sequences outside of the current set timeline.',
        default=False)
    export_meta_subsequences: bpy.props.BoolProperty(
        name='Export From Inside MetaStrips',
        description='Drill into and export all files from inside meta strips as well as the current timeline.',
        default=False)
    videos: bpy.props.EnumProperty(
        name="Videos",
        items=(('NONE', 'Only Export Audio', ""), ("SELECTED", "Export Selected Videos", ""), ("ALL", "Export All Videos", "")),
        default='NONE')

    def execute(self, context):
        if self.filepath:
            if not self.filepath.lower().endswith('.edl'):
                self.filepath = self.filepath + '.edl'
            try:
                export_samplitude_edl(self.filepath, limit_timeline=self.only_current_timeline, meta_sub=self.export_meta_subsequences, videos=self.videos)
                self.report({'INFO'}, "Saved file to: "+self.filepath)
            except Exception as e:
                self.report({'WARNING'}, 'Could not save file: '+self.filepath+', '+str(e))
        else:
            self.report({'WARNING'}, 'No file to export to')
        return{'FINISHED'}


class XMLExport(bpy.types.Operator, ExportHelper):
    bl_idname = "sequencer.xml_export"
    bl_label = "Export VSE XML (.xml)"

    filepath: bpy.props.StringProperty()
    filename_ext = ".xml"
    filter_glob: bpy.props.StringProperty(default="*.xml", options={'HIDDEN'})
    check_extension = True

    def execute(self, context):
        if self.filepath:
            if not self.filepath.lower().endswith('.xml'):
                self.filepath = self.filepath + '.xml'
            try:
                export_timeline_data(self.filepath)
                self.report({'INFO'}, "Saved file to: "+self.filepath)
            except Exception as e:
                self.report({'WARNING'}, 'Could not save file: '+self.filepath+', '+str(e))
        else:
            self.report({'WARNING'}, 'No file to export to')
        return{'FINISHED'}


class XMLImport(bpy.types.Operator, ImportHelper):
    bl_idname = "sequencer.xml_import"
    bl_label = "Import XML to VSE"

    mode: bpy.props.EnumProperty(
        name="Import Mode",
        description="How to import this xml data into the timeline",
        items=(("NEW", "Make New Timeline", ""), ("APPEND", "Add To Timeline", ""), ("REPLACE", "Overwrite Timeline", "")),
        default="APPEND")
    offset_x: bpy.props.IntProperty(
        name="Frame Offset",
        description="Horizontal offset of imported sequences in frames",
        default=0)
    offset_y: bpy.props.IntProperty(
        name="Channel Offset",
        description="Vertical offset of imported sequences in channels",
        default=0)

    filename_ext = ".xml"
    filter_glob: bpy.props.StringProperty(default="*.xml", options={'HIDDEN'})
    filepath: bpy.props.StringProperty()

    def draw(self, context):
        context.space_data.params.use_filter = True
        layout = self.layout
        layout.prop(self, 'mode')
        layout.prop(self, 'offset_x')
        layout.prop(self, 'offset_y')

    def execute(self, context):
        bpy.ops.ed.undo_push()
        sequencer = context.scene.sequence_editor
        if not sequencer:
            context.scene.sequence_editor_create()
        xml_data = Tree.parse(self.filepath)
        root = xml_data.getroot()
        timeline_data = builddata(root)
        import_timeline_data(timeline_data, mode=self.mode, offset_x=self.offset_x, offset_y=self.offset_y)
        return {'FINISHED'}


def export_menu(self, context):
    self.layout.operator("sequencer.xml_export", text="VSE To XML (.xml)")
    self.layout.operator("sequencer.samplitude_export", text="VSE To Samplitude EDL (.edl)")
    self.layout.operator("sequencer.vegas_export", text="VSE To Vegas EDL (.txt)")


def import_menu(self, context):
    self.layout.operator("sequencer.xml_import", text="XML To VSE")


def register():
    bpy.utils.register_class(SamplitudeEDLExport)
    bpy.utils.register_class(VegasEDLExport)
    bpy.utils.register_class(XMLExport)
    bpy.utils.register_class(XMLImport)
    bpy.types.TOPBAR_MT_file_export.append(export_menu)
    bpy.types.TOPBAR_MT_file_import.append(import_menu)


def unregister():
    bpy.utils.unregister_class(SamplitudeEDLExport)
    bpy.utils.unregister_class(VegasEDLExport)
    bpy.utils.unregister_class(XMLExport)
    bpy.utils.unregister_class(XMLImport)
    bpy.types.TOPBAR_MT_file_export.remove(export_menu)
    bpy.types.TOPBAR_MT_file_import.remove(import_menu)


if __name__ == "__main__":
    register()
