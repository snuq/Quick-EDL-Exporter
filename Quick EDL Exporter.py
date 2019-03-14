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

import bpy
import os
import math
from bpy_extras.io_utils import ExportHelper

bl_info = {
    "name": "Quick EDL Exporter",
    "description": "Exports audio (and video) in the VSE timeline to EDL format to be used in Reaper, Samplitude and others.",
    "author": "Hudson Barkley (Snu/snuq/Aritodo)",
    "version": (0, 8, 1),
    "blender": (2, 80, 0),
    "location": "File > Export > Samplitude EDL (.edl); Vegas EDL (.txt)",
    "wiki_url": "",
    "tracker_url": "https://github.com/snuq/Quick-EDL-Exporter/issues",
    "category": "Import-Export"
}


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
    rate_text = bpy.context.user_preferences.system.audio_sample_rate
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
    bl_idname = "edl.vegas_export"
    bl_label = "Export Vegas EDL (.txt)"

    filepath = bpy.props.StringProperty()
    filename_ext = ".txt"
    filter_glob = bpy.props.StringProperty(default="*.txt", options={'HIDDEN'})
    check_extension = True

    only_current_timeline = bpy.props.BoolProperty(
        name='Export Only Current Timeline',
        description='Ignore sequences outside of the current set timeline.',
        default=False)
    export_meta_subsequences = bpy.props.BoolProperty(
        name='Export From Inside MetaStrips',
        description='Drill into and export all files from inside meta strips as well as the current timeline.',
        default=False)
    videos = bpy.props.EnumProperty(
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
    bl_idname = "edl.samplitude_export"
    bl_label = "Export Samplitude EDL (.edl)"

    filepath = bpy.props.StringProperty()
    filename_ext = ".edl"
    filter_glob = bpy.props.StringProperty(default="*.edl", options={'HIDDEN'})
    check_extension = True

    only_current_timeline = bpy.props.BoolProperty(
        name='Export Only Current Timeline',
        description='Ignore sequences outside of the current set timeline.',
        default=False)
    export_meta_subsequences = bpy.props.BoolProperty(
        name='Export From Inside MetaStrips',
        description='Drill into and export all files from inside meta strips as well as the current timeline.',
        default=False)
    videos = bpy.props.EnumProperty(
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


def edl_export_menu(self, context):
    self.layout.operator("edl.samplitude_export", text="Samplitude EDL (.edl)")
    self.layout.operator("edl.vegas_export", text="Vegas EDL (.txt)")


def register():
    bpy.utils.register_class(SamplitudeEDLExport)
    bpy.utils.register_class(VegasEDLExport)
    bpy.types.INFO_MT_file_export.append(edl_export_menu)


def unregister():
    bpy.utils.unregister_class(SamplitudeEDLExport)
    bpy.utils.unregister_class(VegasEDLExport)
    bpy.types.INFO_MT_file_export.remove(edl_export_menu)


if __name__ == "__main__":
    register()
