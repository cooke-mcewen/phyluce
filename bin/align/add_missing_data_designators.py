#!/usr/bin/env python
# encoding: utf-8

"""
add_gaps_for_missing_taxa.py

Created by Brant Faircloth on 05 July 2011.
Copyright 2011 Brant C. Faircloth. All rights reserved.
"""

import os
import sys
import copy
import argparse
import ConfigParser
import multiprocessing
from collections import defaultdict
from Bio import AlignIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.Alphabet import IUPAC, Gapped
from Bio.Align import MultipleSeqAlignment

from phyluce.helpers import FullPaths, CreateDir, is_dir, is_file, get_alignment_files
from phyluce.log import setup_logging

#import pdb


def get_args():
    parser = argparse.ArgumentParser(
        description='Add missing data designators to a set of incomplete alignments.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--alignments',
        required=True,
        type=is_dir,
        action=FullPaths,
        help="Alignment files to process"
    )
    parser.add_argument(
        "--output",
        required=True,
        action=CreateDir,
        help="The output dir in which to store copies of the alignments"
    )
    parser.add_argument(
        '--match-count-output',
        required=True,
        action=FullPaths,
        type=is_file,
        help='The output file containing taxa and loci in complete/incomplete matrices generated by get_match_counts.py.'
    )
    parser.add_argument(
        '--incomplete-matrix',
        required=True,
        action=FullPaths,
        type=is_file,
        help="The output file for incomplete-matrix records generated by get_match_counts.py."
        )
    parser.add_argument(
        '--min-taxa',
        help="The minimum number of taxa to keep",
        default=3,
        type=int
    )
    parser.add_argument(
        '--verbatim',
        action="store_true",
        default=False,
        help="""Do not parse species names at all - use them verbatim""",
    )
    parser.add_argument(
        "--input-format",
        choices=["fasta", "nexus", "phylip", "clustal", "emboss", "stockholm"],
        default="nexus",
        help="""The input alignment format.""",
    )
    parser.add_argument(
        "--output-format",
        choices=["fasta", "nexus", "phylip", "clustal", "emboss", "stockholm"],
        default="nexus",
        help="""The output alignment format.""",
    )
    parser.add_argument(
        "--verbosity",
        type=str,
        choices=["INFO", "WARN", "CRITICAL"],
        default="INFO",
        help="""The logging level to use."""
    )
    parser.add_argument(
        "--log-path",
        action=FullPaths,
        type=is_dir,
        default=None,
        help="""The path to a directory to hold logs."""
    )
    parser.add_argument(
        "--cores",
        type=int,
        default=1,
        help="""Process alignments in parallel using --cores for alignment. """ +
        """This is the number of PHYSICAL CPUs."""
    )
    return parser.parse_args()


def get_names_from_config(log, config, group):
    log.info("Getting taxon names from --match-count-output")
    try:
        return [i[0].rstrip('*') for i in config.items(group)]
    except ConfigParser.NoSectionError:
        return None


def record_formatter(seq, name):
    """return a string formatted as a biopython sequence record"""
    return SeqRecord(
        Seq(seq, Gapped(IUPAC.ambiguous_dna, "-?")),
        id=name,
        name=name,
        description=name
    )


def add_gaps_to_align(aln, organisms, missing, verbatim=False, min_taxa=3):
    local_organisms = copy.deepcopy(organisms)
    if len(aln) < min_taxa:
        new_align = None
    elif len(aln) >= min_taxa:
        new_align = MultipleSeqAlignment([], Gapped(IUPAC.ambiguous_dna, "-?"))
        overall_length = len(aln[0])
        for seq in aln:
            # strip any reversal characters from mafft
            seq.name = seq.name.lstrip('_R_')
            if not verbatim:
                new_seq_name = '_'.join(seq.name.split('_')[1:])
            else:
                new_seq_name = seq.name.lower()
            new_align.append(record_formatter(str(seq.seq), new_seq_name))
            local_organisms.remove(new_seq_name)
        for org in local_organisms:
            if not verbatim:
                loc = '_'.join(seq.name.split('_')[:1])
            else:
                loc = seq.name
            if missing:
                try:
                    assert loc in missing[org], "Locus missing"
                except:
                    assert loc in missing['{}*'.format(org)], "Locus missing"
            missing_string = '?' * overall_length
            new_align.append(record_formatter(missing_string, org))
    return new_align


def get_missing_loci_from_conf_file(config):
    missing = defaultdict(list)
    for sec in config.sections():
        for item in config.items(sec):
            missing[sec].append(item[0])
    return missing


def add_designators(work):
    file, input_format, organisms, missing, verbatim, min_taxa, output, output_format = work
    aln = AlignIO.read(file, input_format)
    new_align = add_gaps_to_align(aln, organisms, missing, verbatim, min_taxa)
    if new_align is not None:
        outf = os.path.join(output, os.path.basename(file))
        AlignIO.write(new_align, open(outf, 'w'), output_format)
        return None
    else:
        return file


def main():
    args = get_args()
    # setup logging
    log, my_name = setup_logging(args)
    # read config file output by match_count_config.py
    config = ConfigParser.RawConfigParser(allow_no_value=True)
    config.read(args.match_count_output)
    # read the incomplete matrix file that contains loci that are incomplete
    if args.incomplete_matrix:
        incomplete = ConfigParser.RawConfigParser(allow_no_value=True)
        incomplete.read(args.incomplete_matrix)
        missing = get_missing_loci_from_conf_file(incomplete)
    else:
        missing = None
    # get the taxa in the alignment
    organisms = get_names_from_config(log, config, 'Organisms')
    # get input files
    files = get_alignment_files(log, args.alignments, args.input_format)
    work = [[
            file,
            args.input_format,
            organisms,
            missing,
            args.verbatim,
            args.min_taxa,
            args.output,
            args.output_format
        ] for file in files
    ]
    log.info("Adding missing data designators using {} cores".format(args.cores))
    if args.cores > 1:
        assert args.cores <= multiprocessing.cpu_count(), "You've specified more cores than you have"
        pool = multiprocessing.Pool(args.cores)
        results = pool.map(add_designators, work)
    else:
        results = map(add_designators, work)
    for result in results:
        if result is not None:
            log.info("Dropped {} because of too few taxa (N < {})".format(
                result,
                args.min_taxa
            ))
    # end
    text = " Completed {} ".format(my_name)
    log.info(text.center(65, "="))


if __name__ == '__main__':
    main()
