#!/usr/bin/env python
# encoding: utf-8
"""
File: get_smilogram_from_alignments.py
Author: Brant Faircloth

Created by Brant Faircloth on 07 August 2012 21:08 PDT (-0700)
Copyright (c) 2012 Brant C. Faircloth. All rights reserved.

Description: Given a folder of alignments, generate a smilogram

"""

import os
import re
import sys
import glob
import sqlite3
import argparse
import multiprocessing
from collections import Counter
from random import choice
from Bio import AlignIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.Alphabet import generic_dna
from Bio.Align import MultipleSeqAlignment
from phyluce.helpers import is_dir, FullPaths, get_file_extensions

import pdb


def get_args():
    parser = argparse.ArgumentParser(
            description="""Record variant positions in alignments"""
        )
    parser.add_argument(
            'input',
            type=is_dir,
            action=FullPaths,
            help="""The directory containing the alignment files"""
        )
    parser.add_argument(
            'output',
            type=str,
            default='output',
            help="""The output filename (without extension - code will add .sqlite)"""
        )
    parser.add_argument(
            "--input-format",
            dest="input_format",
            choices=['fasta', 'nexus', 'phylip', 'clustal', 'emboss', 'stockholm'],
            default='fasta',
            help="""The input alignment format""",
        )
    parser.add_argument(
            "--cores",
            type=int,
            default=1,
            help="""The number of cores to use.""",
        )
    parser.add_argument(
            "--smilogram",
            action="store_true",
            default=False,
            help="""Help text""",
        )
    return parser.parse_args()


def get_files(input_dir, input_format):
    alignments = []
    for ftype in get_file_extensions(input_format):
        alignments.extend(glob.glob(os.path.join(input_dir, "*{}".format(ftype))))
    return alignments


def create_differences_database(db):
    """Create the indel database"""
    conn = sqlite3.connect(db)
    c = conn.cursor()
    c.execute("PRAGMA foreign_keys = ON")
    try:
        c.execute('''CREATE TABLE loci (
                locus text PRIMARY KEY,
                length int
            )'''
        )
        c.execute('''CREATE TABLE by_taxon (
                idx INTEGER PRIMARY KEY AUTOINCREMENT,
                taxon text,
                locus text,
                position int,
                position_from_center int,
                type text,
                FOREIGN KEY (locus) REFERENCES loci(locus)
            )'''
        )
        c.execute('''CREATE TABLE by_locus (
                idx INTEGER PRIMARY KEY AUTOINCREMENT,
                locus text,
                majallele real,
                substitutions real,
                deletions real,
                insertions real,
                missing real,
                bases real,
                position int,
                position_from_center int,
                type text,
                FOREIGN KEY (locus) REFERENCES loci(locus)
            )'''
        )
        c.execute('''CREATE TABLE by_locus_missing (
                idx INTEGER PRIMARY KEY AUTOINCREMENT,
                locus text,
                present real,
                absent real,
                position int,
                position_from_center int,
                type text,
                FOREIGN KEY (locus) REFERENCES loci(locus)
            )'''
        )
    except sqlite3.OperationalError, e:
        #pdb.set_trace()
        if e.message == 'table loci already exists':
            answer = raw_input("Database already exists.  Overwrite [Y/n]? ")
            if answer == "Y" or "YES":
                os.remove(db)
                conn, c = create_differences_database(db)
            else:
                pdb.set_trace()
        else:
            raise sqlite3.OperationalError, e.message
            pdb.set_trace()
    return conn, c


def replace_gaps_at_start_and_ends(seq):
    """walk end from the ends of alignments to locate and replace gaps at ends"""
    begin, end = [], []
    for start, base in enumerate(seq):
        if base == '-':
            begin.append('?')
            continue
        else:
            break
    for stop, base in enumerate(seq[::-1]):
        if base == '-':
            end.append('?')
            continue
        else:
            stop = len(seq) - stop
            break
    newseq = ''.join(begin) + str(seq[start:stop]) + ''.join(end)
    return Seq(newseq, generic_dna)


def replace_gaps(aln):
    """we need to determine actual starts of alignments"""
    new_aln = MultipleSeqAlignment([], generic_dna)
    for taxon in aln:
        seq = replace_gaps_at_start_and_ends(taxon.seq)
        new_aln.append(SeqRecord(seq, id=taxon.id, name=taxon.name, description=taxon.description))
    return new_aln


def worker(work):
    arguments, f = work
    results = {}
    name_map = {}
    base_count = {}
    locus = os.path.splitext(os.path.basename(f))[0]
    aln = AlignIO.read(f, arguments.input_format)
    # map taxon position in alignment to name
    for idx, taxon in enumerate(aln):
        name_map[idx] = taxon.id
        results[taxon.id] = {
                    'insertion': [],
                    'deletion': [],
                    'substitution': [],
                    'majallele':[],
                    None:[]
                }
    # get rid of end gappiness, since that makes things a problem
    # for indel ID. Substitute "?" at the 5' and 3' gappy ends.
    # we assume internal gaps are "real" whereas end gaps usually
    # represent missing data.
    aln = replace_gaps(aln)
    for idx in xrange(aln.get_alignment_length()):
        col = aln[:, idx]
        # strip the "n" or "N"
        bases = re.sub('N|n|\?', "", col)
        # count total number of sites considered
        base_count[idx] = len(bases)
        # if all the bases are replace N|n|?, skip
        if len(bases) == 0:
            pass
        # if there is only 1 base, make it the major allele
        elif len(set(bases)) == 1:
            major = bases[0].lower()
        # if there are multiple alleles, pick the major allele
        else:
            # count all the bases in a column
            count = Counter(bases)
            # get major allele if possible
            count_of_count = Counter(count.values())
            # we can't have a tie
            if count_of_count[max(count_of_count)] == 1:
                major = count.most_common()[0][0]
            else:
                # randomly select a major allele (excluding gaps)
                # when there is a tie
                common_bases = []
                for base, c in count.most_common():
                    base = base.lower()
                    # bases can be any of IUPAC set except N|n
                    if c == max(count_of_count) and base in ['a', 'c', 't', 'g', 'r', 'y', 's', 'w', 'k', 'm', 'b', 'd', 'h', 'v']:
                        common_bases.append(base)
                # randomly select 1 of the bases
                major = choice(common_bases)
            # now, check for indels/substitutions
        for pos, base in enumerate(col):
            base = base.lower()
            if (base in ['N', 'n', '?']):
                results[name_map[pos]][None].append(idx)
            elif (base == major):
                results[name_map[pos]]['majallele'].append(idx)
            elif major == '-' and base != '-':
                results[name_map[pos]]['insertion'].append(idx)
            elif base == '-' and major != '-':
                results[name_map[pos]]['deletion'].append(idx)
            elif base != '-' and major != '-':
                results[name_map[pos]]['substitution'].append(idx)
    sys.stdout.write('.')
    sys.stdout.flush()
    return (locus, results, aln.get_alignment_length(), base_count)


def main():
    args = get_args()
    db_name = "{0}.sqlite".format(args.output)
    conn, c = create_differences_database(db_name)
    # iterate through all the files to determine the longest alignment
    work = [(args, f) for f in get_files(args.input, args.input_format)]
    sys.stdout.write("Running")
    if args.cores > 1:
        pool = multiprocessing.Pool(args.cores)
        results = pool.map(worker, work)
    else:
        results = map(worker, work)
    print "\nEntering data to sqlite...."
    # fill the individual/locus/position specific table
    for locus, result, length, bases in results:
        # get approximate center of alignment
        center = length / 2
        # fill locus table
        c.execute('''INSERT INTO loci VALUES (?,?)''', (locus, length))
        # fill the position specific table
        for taxon_name, values in result.iteritems():
            for typ, positions in values.iteritems():
                if positions != []:
                    #pdb.set_trace()
                    for pos in positions:
                        c.execute('''INSERT INTO by_taxon (
                            taxon,
                            locus,
                            position,
                            position_from_center,
                            type
                        )
                        VALUES (?,?,?,?,?)''', (
                            taxon_name,
                            locus,
                            pos,
                            pos - center,
                            typ
                        ))
        # we also want a locus specific list of all variable positions
        # basically we'll use this to generate the distro of variable
        # positions relative to centerline of the UCE (AKA the "smilogram")
        #
        # NOTE:  currently only doing this for substitutions
        maj, subs, dels, ins, n = [],[],[],[],[]
        # get all substitution locations across individuals
        for k, v in result.iteritems():
            maj.extend(v['majallele'])
            subs.extend(v['substitution'])
            dels.extend(v['deletion'])
            ins.extend(v['insertion'])
            n.extend(v[None])
        # get a count of variability by position in BP
        maj_cnt = Counter(maj)
        subs_cnt = Counter(subs)
        dels_cnt = Counter(dels)
        ins_cnt = Counter(ins)
        n_cnt = Counter(n)
        # iterate over counts of all positions - having subs and not having subs
        # then add those + any sub location to the DB
        for pos in sorted(bases.keys()):
            c.execute('''INSERT INTO by_locus (
                    locus,
                    majallele,
                    substitutions,
                    deletions,
                    insertions,
                    missing,
                    bases,
                    position,
                    position_from_center,
                    type
                )
                VALUES (?,?,?,?,?,?,?,?,?,?)''', (
                    locus,
                    maj_cnt[pos],
                    subs_cnt[pos],
                    dels_cnt[pos],
                    ins_cnt[pos],
                    n_cnt[pos],
                    bases[pos],
                    pos,
                    pos - center,
                    'substitutions'
                    ))
            c.execute('''INSERT INTO by_locus_missing (
                    locus,
                    present,
                    absent,
                    position,
                    position_from_center,
                    type
                )
                VALUES (?,?,?,?,?,?)''', (
                    locus,
                    bases[pos],
                    len(result.keys()) - bases[pos],
                    pos,
                    pos - center,
                    'missing'
                    ))
    conn.commit()
    if args.smilogram:
        # get data for substitution smilogram
        outf = open("{0}-smilogram.csv".format(args.output), 'w')
        outf.write('substitutions,bp,freq,distance_from_center\n')
        c.execute('''CREATE TEMP TABLE ssb AS
            SELECT sum(substitutions) AS ss, sum(bases), sum(substitutions)/sum(bases), position_from_center
            FROM by_locus GROUP BY position_from_center
            ''')
        c.execute('''SELECT * FROM ssb WHERE ss != 0''')
        results = c.fetchall()
        for row in results:
            outf.write("{0}\n".format(','.join(map(str, row))))
        outf.close()
        
        # get data for missing data smilogram
        outf = open("{0}-missing.csv".format(args.output), 'w')
        outf.write('substitutions,bp,freq,distance_from_center\n')
        c.execute('''CREATE TEMP TABLE ssc AS
            SELECT sum(present) as pres, sum(absent), sum(absent)/(sum(absent) + sum(present)), position_from_center
            FROM by_locus_missing GROUP BY position_from_center
            ''')
        c.execute('''SELECT * FROM ssc WHERE pres != 0''')
        results = c.fetchall()
        for row in results:
            outf.write("{0}\n".format(','.join(map(str, row))))
        outf.close()
    c.close()
    conn.close()
    print "Done."


if __name__ == '__main__':
    main()
