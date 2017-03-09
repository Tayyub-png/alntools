# -*- coding: utf-8 -*-
from collections import OrderedDict, namedtuple
from struct import pack
import multiprocessing
import os
import struct
import sys
import time

import utils

from Bio import bgzf
from emase import AlignmentPropertyMatrix as APM
import pysam

try:
    xrange
except NameError:
    xrange = range


LOG = utils.get_logger()
BAM_HEADER = "\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff\x06\x00\x42\x43\x02\x00"
BAM_EOF = "\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff\x06\x00BC\x02\x00\x1b\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00"

parse_fields = ["header_size", "begin_read_offset", "begin_read_size", "file_offset", "file_bytes", "end_read_offset",
                "end_read_size"]
ParseRecord = namedtuple("ParseRecord", parse_fields)


class MPParams(object):
    """
    # each core needs
    # - name of alignment file
    # - header size
    # - target file
    # - list of files to create and work on
    #   - idx, vo_start, vo_end

    """
    slots = ['input_file', 'target_file', 'temp_dir', 'process_id', 'data', 'emase']

    def __init__(self):
        self.input_file = None
        self.target_file = None
        self.temp_dir = None
        self.process_id = None
        self.data = [] # tuple of (idx, ParseRecord)
        self.emase = False

    def __str__(self):
        return "Input: {}\nProcess ID: {}".format(self.input_file, self.process_id)


class ConvertResults(object):
    slots = ['main_targets', 'ec', 'ec_idx', 'haplotypes', 'target_idx_to_main_target', 'unique_tids', 'unique_reads', 'init']

    def __init__(self):
        self.main_targets = None
        self.ec = None
        self.ec_idx = None
        self.haplotypes = None
        self.target_idx_to_main_target = None
        self.unique_tids = None
        self.unique_reads = None
        self.init = False


def get_header_size(bam_filename):
    #
    # grab header
    #
    alignment_file = pysam.AlignmentFile(bam_filename)
    header_size = alignment_file.tell() >> 16
    alignment_file.close()
    return header_size


def fix_bam(filename):
    """
    Make sure the EOF marker is present.

    :param filename: the name of the BAME file
    :return: Nothing
    """
    if not os.path.isfile(filename):
        sys.exit("Missing file {}".format(filename))

    size = os.path.getsize(filename)
    h = open(filename, "rb")

    # Check it looks like a BGZF file
    # (could still be GZIP'd, in which case the extra block is harmless)
    data = h.read(len(BAM_HEADER))

    if data != BAM_HEADER:
        sys.exit("File {} is not a BAM file".format(filename))

    # Check if it has the EOF already
    h.seek(size - 28)
    data = h.read(28)
    h.close()

    if data != BAM_EOF:
        # Adding EOF block
        h = open(filename, "ab")
        h.write(BAM_EOF)
        h.close()

def process_piece(mp):
    """

    :return:
    """
    LOG.debug('Process ID: {}, Input File: {}'.format(mp.process_id, mp.input_file))

    if mp.target_file:
        LOG.debug('Process ID: {}, Target File: {}'.format(mp.process_id, mp.target_file))

    if mp.emase:
        LOG.debug('Process ID: {}, Emase format requested'.format(mp.process_id))

    try:
        sam_file = pysam.Samfile(mp.input_file, 'rb')
        if len(sam_file.header) == 0:
            raise Exception("BAM File has no header information")
    except:
        sam_file = pysam.Samfile(mp.input_file, 'r')
        if len(sam_file.header) == 0:
            raise Exception("SAM File has no header information")

    main_targets = OrderedDict()

    if mp.target_file:
        main_targets = utils.parse_targets(mp.target_file)
        if len(main_targets) == 0:
            LOG.error("Unable to parse target file")
            sys.exit(-1)
    else:
        tmp = {}
        for target in sam_file.references:
            idx_underscore = target.rfind('_')
            main_target = target[:idx_underscore]
            if main_target not in tmp:
                tmp[main_target] = main_target
        main_targets_tmp = sorted(tmp.keys())
        for t in main_targets_tmp:
            main_targets[t] = len(main_targets)

    sam_file.close()

    # ec = equivalence class
    #      the KEY is a comma separated string of tids
    #      the VALUE is the number of times this equivalence class has appeared
    ec = OrderedDict()

    # ec_idx = lookup to ec
    #          the KEY is a comma separated string of tids
    #          the VALUE is a number specifying the insertion order of the KEY value in ec
    ec_idx = {}

    # all the haplotypes
    haplotypes = set()

    # a lookup of tids to main_targets (Ensembl IDs)
    target_idx_to_main_target = {}

    # unique number of tids encountered and the count
    unique_tids = {}

    # unique reads
    unique_reads = {}

    # times encountering new read id
    read_id_switch_counter = 0

    same_read_target_counter = 0


    #pid = os.getpid()

    all_alignments = 0
    valid_alignments = 0
    ec_key = None
    tid = None

    target_ids = []
    temp_name = os.path.join(mp.temp_dir, '_bam2ec.')

    try:
        for file_info_data in mp.data:
            try:
                idx = file_info_data[0]
                parse_record = file_info_data[1]

                # must create the file
                temp_file = "{}{}.bam".format(temp_name, idx)
                LOG.debug("Process ID: {}, Creating alignment file: {}".format(mp.process_id, temp_file))
                utils.delete_file(temp_file)
                chunk_file(mp.input_file, temp_file, parse_record)
                LOG.debug("Process ID: {}, Opening alignment file: {}".format(mp.process_id, temp_file))

                sam_file = pysam.AlignmentFile(temp_file)
                tell = sam_file.tell()

                read_id = None

                while True:
                    alignment = sam_file.next()

                    all_alignments += 1

                    # reference_sequence_name = Column 3 from file, the Reference NAME (EnsemblID_Haplotype)
                    # tid = the target id, which is 0 or a positive integer mapping to entries
                    #       within the sequence dictionary in the header section of a BAM file
                    # main_target = the Ensembl id of the transcript

                    # if alignment.flag == 4 or alignment.is_unmapped:
                    if alignment.is_unmapped:
                        continue

                    valid_alignments += 1


                    reference_sequence_name = sam_file.getrname(alignment.tid)
                    tid = str(alignment.tid)
                    idx_underscore = reference_sequence_name.rfind('_')
                    main_target = reference_sequence_name[:idx_underscore]

                    try:
                        unique_tids[tid] += 1
                    except KeyError:
                        unique_tids[tid] = 1

                    if mp.target_file:
                        if main_target not in main_targets:
                            LOG.error("Unexpected target found in BAM file: {}".format(main_target))
                            sys.exit(-1)
                    #else:
                    #    if main_target not in main_targets:
                    #        main_targets[main_target] = len(main_targets)

                    target_idx_to_main_target[tid] = main_target

                    try:
                        haplotype = reference_sequence_name[idx_underscore+1:]
                        haplotypes.add(haplotype)
                    except:
                        LOG.info('Unable to parse Haplotype from {}'.format(reference_sequence_name))
                        return

                    # read_id = Column 1 from file, the Query template NAME
                    if read_id is None:
                        read_id = alignment.qname

                    try:
                        unique_reads[read_id] += 1
                    except KeyError:
                        unique_reads[read_id] = 1

                    if read_id != alignment.qname:
                        ec_key = ','.join(sorted(target_ids))

                        try:
                            ec[ec_key] += 1
                        except KeyError:
                            ec[ec_key] = 1
                            ec_idx[ec_key] = len(ec_idx)

                        read_id = alignment.qname
                        target_ids = [tid]
                        read_id_switch_counter += 1
                    else:
                        if tid not in target_ids:
                            target_ids.append(tid)
                        else:
                            same_read_target_counter += 1

                    if all_alignments % 100000 == 0:
                        LOG.debug("Process ID: {}, File: {}, {:,} valid alignments processed out of {:,}, with {:,} equivalence classes".format(mp.process_id, temp_file, valid_alignments, all_alignments, len(ec)))

            except StopIteration:
                LOG.info(
                    "DONE Process ID: {}, File: {}, {:,} valid alignments processed out of {:,}, with {:,} equivalence classes".format(
                        mp.process_id, temp_file, valid_alignments, all_alignments, len(ec)))


            #LOG.info("{0:,} alignments processed, with {1:,} equivalence classes".format(line_no, len(ec)))
            if tid not in target_ids:
                target_ids.append(tid)
            else:
                same_read_target_counter += 1

            ec_key = ','.join(sorted(target_ids))

            try:
                ec[ec_key] += 1
            except KeyError:
                ec[ec_key] = 1
                ec_idx[ec_key] = len(ec_idx)

            utils.delete_file(temp_file)

    except Exception as e:
        LOG.error("Error: {}".format(str(e)))

    haplotypes = sorted(list(haplotypes))

    LOG.debug("# Unique Reads: {:,}".format(len(unique_reads)))
    LOG.debug("# Reads/Target Duplications: {:,}".format(same_read_target_counter))
    LOG.debug("# Main Targets: {:,}".format(len(main_targets)))
    LOG.debug("# Haplotypes: {:,}".format(len(haplotypes)))
    LOG.debug("# Unique Targets: {:,}".format(len(unique_tids)))
    LOG.debug("# Equivalence Classes: {:,}".format(len(ec)))

    ret = ConvertResults()
    ret.main_targets = main_targets
    ret.ec = ec
    ret.ec_idx = ec_idx
    ret.haplotypes = haplotypes
    ret.target_idx_to_main_target = target_idx_to_main_target
    ret.unique_tids = unique_tids
    ret.unique_reads = unique_reads

    return ret


def wrapper(args):
    """
    Simple wrapper, useful for debugging.

    :param args: the arguments to process_piece
    :return: the same as process_piece
    """
    #print str(args)
    return process_piece(*args)


def convert(bam_filename, output_filename, num_chunks=0, target_filename=None, emase=False, temp_dir=None):
    """
    """
    start_time = time.time()

    num_processes = multiprocessing.cpu_count()

    if num_chunks <= 0:
        num_chunks = num_processes
    else:
        if num_chunks > 1000:
            LOG.info("Modifying number of chunks from {} to 1000".format(num_chunks))
            num_chunks = 1000

    if not temp_dir:
        temp_dir = os.path.dirname(output_filename)

    LOG.info("Calculating {:,} chunks".format(num_chunks))
    temp_time = time.time()
    chunks = calculate_chunks(bam_filename, num_chunks)
    LOG.info("{:,} chunks calculated in {}, total time: {}".format(len(chunks),
                                                                   utils.format_time(temp_time, time.time()),
                                                                   utils.format_time(start_time, time.time())))

    # each core needs
    # - name of alignment file
    # - target file
    # - list of files to create and work on
    #   - idx, mp_param

    all_params = []

    for temp_chunk_ids in utils.partition([idx for idx in xrange(num_chunks)], num_processes):
        params = MPParams()
        params.input_file = bam_filename
        params.target_file = target_filename
        params.temp_dir = temp_dir

        for x, cid in enumerate(temp_chunk_ids):
            params.process_id = str(cid)
            params.data.append((cid, chunks[cid]))

        all_params.append(params)

    final = ConvertResults()
    final.ec = OrderedDict()
    final.ec_idx = {}
    final.haplotypes = set()
    final.main_targets = OrderedDict()
    final.target_idx_to_main_target = {}
    final.unique_tids = {}
    final.unique_reads = {}

    LOG.info("Starting {} processes ...".format(num_processes))

    temp_time = time.time()
    args = zip(all_params)
    pool = multiprocessing.Pool(num_processes)
    results = pool.map(wrapper, args)

    LOG.info("All processes done in {}, total time: {}".format(utils.format_time(temp_time, time.time()),
                                                               utils.format_time(start_time, time.time())))

    LOG.info("Combining {} results ...".format(len(results)))
    temp_time = time.time()

    alignment_file = pysam.AlignmentFile(bam_filename)

    # parse results
    for idx, result in enumerate(results):
        if not final.init:
            final = result
            final.init = True
        else:
            # combine ec
            for k, v in result.ec.iteritems():
                if k in final.ec:
                    final.ec[k] += v
                else:
                    final.ec[k] = v
                    final.ec_idx[k] = len(final.ec_idx)

            # combine haplotypes
            s1 = set(final.haplotypes)
            s2 = set(result.haplotypes)
            final.haplotypes = sorted(list(s1.union(s2)))

            # combine target_idx_to_main_target
            for k, v in result.target_idx_to_main_target.iteritems():
                if k not in final.target_idx_to_main_target:
                    final.target_idx_to_main_target[k] = v

            # unique reads
            for k, v in result.unique_reads.iteritems():
                if k in final.unique_reads:
                    final.unique_reads[k] += v
                else:
                    final.unique_reads[k] = v

        LOG.debug("CHUNK {}: results combined in {}, total time: {}".format(idx, utils.format_time(temp_time, time.time()),
                 utils.format_time(start_time, time.time())))

    LOG.info("All results combined in {}, total time: {}".format(utils.format_time(temp_time, time.time()),
             utils.format_time(start_time, time.time())))

    LOG.info("# Unique Reads: {:,}".format(len(final.unique_reads)))
    #print "# Reads/Target Duplications: {:,}".format(same_read_target_counter)
    LOG.info( "# Main Targets: {:,}".format(len(final.main_targets)))
    LOG.info( "# Haplotypes: {:,}".format(len(final.haplotypes)))
    LOG.info( "# Unique Targets: {:,}".format(len(final.unique_tids)))
    LOG.info( "# Equivalence Classes: {:,}".format(len(final.ec)))


    try:
        os.remove(output_filename)
    except OSError:
        pass

    if emase:
        try:
            temp_time = time.time()
            LOG.info('Constructing APM structure...')

            new_shape = (len(final.main_targets), len(final.haplotypes), len(final.ec))

            ec_ids = [x for x in xrange(0, len(final.ec))]

            LOG.debug('Shape={}'.format(new_shape))

            apm = APM(shape=new_shape, haplotype_names=final.haplotypes, locus_names=final.main_targets.keys(), read_names=ec_ids)

            # ec.values -> the number of times this equivalence class has appeared
            apm.count = final.ec.values()

            # k = comma seperated string of tids
            # v = the count
            for k, v in final.ec.iteritems():
                arr_target_idx = k.split(",")

                # get the main targets by name
                temp_main_targets = set()
                for idx in arr_target_idx:
                    temp_main_targets.add(final.target_idx_to_main_target[idx])

                # loop through the targets and haplotypes to get the bits
                for main_target in temp_main_targets:
                    # main_target is not an index, but a value like 'ENMUST..001'

                    for i, hap in enumerate(final.haplotypes):
                        read_transcript = '{}_{}'.format(main_target, hap) # now 'ENMUST..001_A'
                        # get the numerical tid corresponding to read_transcript
                        read_transcript_idx = str(alignment_file.gettid(read_transcript))

                        if read_transcript_idx in arr_target_idx:
                            LOG.debug("{}\t{}\t{}".format(final.ec_idx[k], final.main_targets[main_target], i))

                            # main_targets[main_target] = idx of main target
                            # i = the haplotype
                            # ec_idx[k] = index of ec
                            apm.set_value(final.main_targets[main_target], i, final.ec_idx[k], 1)

            LOG.info("APM Created in {}, total time: {}".format(utils.format_time(temp_time, time.time()),
                                                                utils.format_time(start_time, time.time())))

            temp_time = time.time()
            LOG.info("Flushing to disk...")
            apm.finalize()
            apm.save(output_filename, title='bam2ec')
            LOG.info("{} created in {}, total time: {}".format(output_filename,
                                                               utils.format_time(temp_time, time.time()),
                                                               utils.format_time(start_time, time.time())))

        except Exception as e:
            LOG.fatal("ERROR: {}".format(str(e)))
            raise Exception(e)
    else:
        try:
            temp_time = time.time()
            LOG.info("Generating BIN file...")

            f = open(output_filename, "wb")

            # version
            f.write(pack('<i', 1))
            LOG.info("VERSION: 1")

            # targets
            LOG.info("NUMBER OF TARGETS: {:,}".format(len(final.main_targets)))
            f.write(pack('<i', len(final.main_targets)))
            for main_target, idx in final.main_targets.iteritems():
                LOG.debug("{:,}\t{}\t# {:,}".format(len(main_target), main_target, idx))
                f.write(pack('<i', len(main_target)))
                f.write(pack('<{}s'.format(len(main_target)), main_target))

            # haplotypes
            LOG.info("NUMBER OF HAPLOTYPES: {:,}".format(len(final.haplotypes)))
            f.write(pack('<i', len(final.haplotypes)))
            for idx, hap in enumerate(final.haplotypes):
                LOG.debug("{:,}\t{}\t# {:,}".format(len(hap), hap, idx))
                f.write(pack('<i', len(hap)))
                f.write(pack('<{}s'.format(len(hap)), hap))

            # equivalence classes
            LOG.info("NUMBER OF EQUIVALANCE CLASSES: {:,}".format(len(final.ec)))
            f.write(pack('<i', len(final.ec)))
            for idx, k in enumerate(final.ec.keys()):
                # ec[k] is the count
                LOG.debug("{:,}\t# {}\t{:,}".format(final.ec[k], k, idx))
                f.write(pack('<i', final.ec[k]))

            LOG.debug("Determining mappings...")

            # equivalence class mappings
            counter = 0
            for k, v in final.ec.iteritems():
                arr_target_idx = k.split(",")

                # get the main targets by name
                temp_main_targets = set()
                for idx in arr_target_idx:
                    temp_main_targets.add(final.target_idx_to_main_target[idx])

                counter += len(temp_main_targets)

            LOG.info("NUMBER OF EQUIVALANCE CLASS MAPPINGS: {:,}".format(counter))
            f.write(pack('<i', counter))

            for k, v in final.ec.iteritems():
                arr_target_idx = k.split(",")

                # get the main targets by name
                temp_main_targets = set()
                for idx in arr_target_idx:
                    temp_main_targets.add(final.target_idx_to_main_target[idx])

                # loop through the haplotypes and targets to get the bits
                for main_target in temp_main_targets:
                    # main_target is not an index, but a value like 'ENMUST..001'

                    bits = []

                    for hap in final.haplotypes:
                        read_transcript = '{}_{}'.format(main_target, hap) # now 'ENMUST..001_A'
                        read_transcript_idx = str(alignment_file.gettid(read_transcript))

                        if read_transcript_idx in arr_target_idx:
                            bits.append(1)
                        else:
                            bits.append(0)

                    LOG.debug("{}\t{}\t{}\t# {}\t{}".format(final.ec_idx[k], final.main_targets[main_target], utils.list_to_int(bits), main_target, bits))
                    f.write(pack('<i', final.ec_idx[k]))
                    f.write(pack('<i', final.main_targets[main_target]))
                    f.write(pack('<i', utils.list_to_int(bits)))

            f.close()

            LOG.info("{} created in {}, total time: {}".format(output_filename,
                                                               utils.format_time(temp_time, time.time()),
                                                               utils.format_time(start_time, time.time())))

        except Exception as e:
            LOG.error("Error: {}".format(str(e)))



def split_bam(bam_filename, number_files, output_dir=None):
    """
    Split a BAM file

    :param bam_filename: the name of the BAM file
    :param number_files: number of files to chunk into
    :param output_dir: output directory, defaults to bam_filename directory
    :return:
    """
    start_time = time.time()

    LOG.debug("BAM File: {}".format(bam_filename))
    LOG.debug("Number of Files: {}".format(number_files))

    if not output_dir:
        output_dir = os.path.dirname(bam_filename)

    LOG.debug("Output Directory: {}".format(output_dir))

    bam_basename = os.path.basename(bam_filename)
    bam_prefixname, bam_extension = os.path.splitext(bam_basename)
    bam_output_temp = os.path.join(output_dir, bam_prefixname)

    LOG.info("Calculating {:,} chunks...".format(number_files))
    temp_time = time.time()
    chunks = calculate_chunks(bam_filename, number_files)
    LOG.info("{:,} chunks calculated in {}, total time: {}".format(len(chunks),
                                                                   utils.format_time(temp_time, time.time()),
                                                                   utils.format_time(start_time, time.time())))

    for idx, chunk in enumerate(chunks):
        # must create the file
        new_file = "{}_{}{}".format(bam_output_temp, idx, bam_extension)
        LOG.debug("Creating alignment file: {}".format(new_file))
        chunk_file(bam_filename, new_file, chunk)

    LOG.info("{:,} files created in {}, total time: {}".format(len(chunks),
                                                               utils.format_time(temp_time, time.time()),
                                                               utils.format_time(start_time, time.time())))


def bytes_from_file_bam(read_filename, write_filename, offset=0, bytes_size=-1):
    """
    Read bytes from a file and append them onto another file.

    :param read_filename: the name of the file to read from
    :param write_filename: the name of the file to write to
    :param offset: the number of bytes to offset (seek)
    :param bytes_size: the number of bytes to read, -1 = to end of file
    :return:
    """
    try:

        with open(read_filename, "rb") as fr:
            if offset > 0:
                fr.seek(offset)

            if bytes_size > 0:
                data = fr.read(bytes_size)
            else:
                data = fr.read()

            mode = 'r+b'
            size = os.path.getsize(write_filename)

            with open(write_filename, mode) as fw:
                fw.seek(size - 28)
                temp = fw.read()
                if temp == BAM_EOF:
                    fw.seek(size - 28)
                # else:
                #    fw.seek(size)
                fw.write(data)
    except Exception as e:
        print 'ERROR : ', str(e)



"""
import pysam
from Bio import bgzf
fhin = "../bam2ec/data/smaller_file/bowtie.bam"
fhout = open("tells", "w")
try:
    aln_file = pysam.AlignmentFile(fhin)


    i = 1
    while True:
        tell = aln_file.tell()
        aln = aln_file.next()
        data = bgzf.split_virtual_offset(tell)
        fhout.write("{}\t{}\t{}\t{}\t{}\t{}\n".format(i, data[0], data[1], tell, aln.qname, aln.tid))
        i += 1
except StopIteration:
    pass
fhout.close()
"""


"""





"""



def calculate_chunks(filename, num_chunks):
    """
    Calculate the boundaries in the BAM file and partition into chunks.

    :param filename: name of the BAM file
    :param num_chunks: number of chunks to partition the boundaries into
    :return: a list of tuples containing the start and end boundaries
    """
    if num_chunks == 1:
        return [[0, -1]]

    try:
        f = open(filename, 'r')
        # get all the block start offsets
        block_offsets = []
        decompressed_lengths = []
        i = 0

        for values in FastBgzfBlocks(f):
        #for values in bgzf.BgzfBlocks(f):

            block_offsets.append(values[0])
            decompressed_lengths.append(values[3])

            if i % 10000 == 0:
                LOG.debug('Chunk {}'.format(i))
            i = i + 1

        # partition the starts into manageable chunks
        div, mod = divmod(len(block_offsets), num_chunks)

        aln_file = pysam.AlignmentFile(filename)
        header_size = bgzf.split_virtual_offset(aln_file.tell())[0]
        partitioned_offsets = [(header_size, 0)]

        for i in xrange(1, num_chunks):
            index = div * i + min(i, mod)
            virtual_offset = bgzf.make_virtual_offset(block_offsets[index], 0)
            aln_file.seek(virtual_offset)
            aln = aln_file.next()
            aln_first = aln

            while aln.qname == aln_first.qname:
                virtual_offset = aln_file.tell()
                aln = aln_file.next()

            partitioned_offsets.append(bgzf.split_virtual_offset(virtual_offset))

        aln_file.close()

        # now let's calculate beginning and ends
        params = []

        for i, offset in enumerate(partitioned_offsets):
            #print '{} => {}'.format(i, offset)

            index = block_offsets.index(partitioned_offsets[i][0])
            begin_read_offset = 0
            begin_read_size = 0
            file_offset = 0
            file_bytes = 0
            end_read_offset = 0
            end_read_size = 0

            if i == 0:
                # first
                begin_read_offset = 0
                begin_read_size = 0
                file_offset = block_offsets[index]
                #print 'file_offset=', file_offset
                file_bytes = partitioned_offsets[i + 1][0] - file_offset
                #print 'file_bytes=', file_bytes
                end_read_offset = bgzf.make_virtual_offset(partitioned_offsets[i + 1][0], 0)
                end_read_size = partitioned_offsets[i + 1][1]
            elif i == num_chunks - 1:
                # last
                begin_read_offset = bgzf.make_virtual_offset(partitioned_offsets[i][0], partitioned_offsets[i][1])
                begin_read_size = decompressed_lengths[index] - partitioned_offsets[i][1]
                file_offset = block_offsets[index + 1]
                file_bytes = -1
                end_read_offset = 0
                end_read_size = 0
            else:
                # all others
                if offset[1] == 0:
                    # bgzf boundary
                    print '****************HUH'
                    return

                begin_read_offset = bgzf.make_virtual_offset(partitioned_offsets[i][0], partitioned_offsets[i][1])
                begin_read_size = decompressed_lengths[index] - partitioned_offsets[i][1]
                file_offset = block_offsets[index + 1]
                file_bytes = partitioned_offsets[i + 1][0] - file_offset
                end_read_offset = bgzf.make_virtual_offset(partitioned_offsets[i + 1][0], 0)
                end_read_size = partitioned_offsets[i + 1][1]

            pr = ParseRecord(header_size, begin_read_offset, begin_read_size, file_offset, file_bytes, end_read_offset,
                             end_read_size)
            params.append(pr)

        return params

    except Exception as e:
        print 'calculate_chunks error: {}'.format(str(e))



def truncate_bam_file(fname):
    """
    Remove the EOF from BGZF/BAM file.

    Does not check if the hEOF is present or not.

    :param fname: the name of the BZF/BAM file
    :return:
    """
    utils.truncate_file(fname, 28)


def FastBgzfBlocks(handle):
    """
    Faster version of bgzf.BgzfBlocks

    :param handle: the handle to the BGZF file
    :return: tuple of start offset, block length, data offset, data length
    """
    data_start = 0
    while True:
        start_offset = handle.tell()
        # This may raise StopIteration which is perfect here
        block_length, data_len = _quick_bgzf_load(handle)
        yield start_offset, block_length, data_start, data_len
        data_start += data_len


def _quick_bgzf_load(handle):
    """
    Quicker version of bgzf._bgzf_load.  No decompressing of BGZF data.  Just getting meta information.
    """
    magic = handle.read(4)

    if not magic:
        raise StopIteration

    if magic != bgzf._bgzf_magic:
        raise ValueError("A BGZF block should start with %r, not %r; handle.tell() now says %r" % (bgzf._bgzf_magic, magic, handle.tell()))
    gzip_mod_time, gzip_extra_flags, gzip_os, extra_len = struct.unpack("<LBBH", handle.read(8))
    block_size = None
    x_len = 0
    while x_len < extra_len:
        subfield_id = handle.read(2)
        subfield_len = struct.unpack("<H", handle.read(2))[0]
        subfield_data = handle.read(subfield_len)
        x_len += subfield_len + 4
        if subfield_id == bgzf._bytes_BC:
            assert subfield_len == 2, "Wrong BC payload length"
            assert block_size is None, "Two BC subfields?"
            block_size = struct.unpack("<H", subfield_data)[0] + 1
    assert x_len == extra_len, (x_len, extra_len)
    assert block_size is not None, "Missing BC, this isn't a BGZF file!"
    deflate_size = block_size - 1 - extra_len - 19
    handle.seek(handle.tell() + deflate_size)
    expected_crc = handle.read(4)
    expected_size = struct.unpack("<I", handle.read(4))[0]
    return block_size, expected_size


def chunk_file(bam_filename, new_filename, parse_rec):
    """
    Create a new BAM file from an existing one.

    :param bam_filename: the name of the original BAM file
    :param new_filename: the name of the new BAM file
    :param parse_rec: the information containing where to extract
    :return:
    """
    try:
        os.remove(new_filename)
    except Exception as e:
        pass

    # copy the header from original BAM file to new
    utils.bytes_from_file(bam_filename, new_filename, 0, parse_rec.header_size)

    if parse_rec.begin_read_offset > 0:
        # if there are reads before a chunk offset, we need to extract them
        b = bgzf.BgzfReader(bam_filename)
        b2 = bgzf.BgzfWriter(new_filename, mode="a")
        b.seek(parse_rec.begin_read_offset)
        b2.write(b.read(parse_rec.begin_read_size))
        b2.close()
        truncate_bam_file(new_filename)

    # grab bgzf chunks from the OLD BAM file and append to NEW BAM file
    bytes_from_file_bam(bam_filename, new_filename, parse_rec.file_offset, parse_rec.file_bytes)

    if parse_rec.end_read_offset > 0:
        # if there are reads after a chunk offset, we need to extract them
        b = bgzf.BgzfReader(bam_filename)
        b2 = bgzf.BgzfWriter(new_filename, mode="a")
        b.seek(parse_rec.end_read_offset)
        b2.write(b.read(parse_rec.end_read_size))
        b2.close()

    # fix the bam EOF if needed
    fix_bam(new_filename)

