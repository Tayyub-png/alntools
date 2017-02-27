# -*- coding: utf-8 -*-
from __future__ import print_function
import time
import click

from . import alntools
from . import utils
from . import __logo_text__, __version__


CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version=__version__, message=__logo_text__)
def cli():
    """
    alntools

    Simple tools for alignment

    """


@cli.command('split', options_metavar='<options>', short_help='split a BAM file into many')
@click.argument('bam_file', metavar='bam_file', type=click.Path(exists=True, resolve_path=True, dir_okay=False))
@click.argument('number', metavar='number', type=int)
@click.option('-b', '--boundary', is_flag=True, help='make sure splitting contains consecutive reads')
@click.option('-d', '--directory', type=click.Path(exists=True, resolve_path=True, file_okay=False, dir_okay=True, writable=True), help="output directory")
@click.option('-v', '--verbose', count=True, help='the more times listed, the more output')
def split(bam_file, number, boundary, directory, verbose):
    """
    Convert a BAM file (bam_file) to an EC file (ec_file).
    """
    utils.configure_logging(verbose)
    alntools.split_bam(bam_file, number, boundary, directory)


@cli.command('bam2ec', options_metavar='<options>', short_help='convert a BAM file to EC')
@click.argument('bam_file', metavar='bam_file', type=click.Path(exists=True, resolve_path=True, dir_okay=False))
@click.argument('ec_file', metavar='ec_file', type=click.Path(resolve_path=True, dir_okay=False, writable=True))
@click.option('-c', '--chunks', default=0, help="number of chunks to process")
@click.option('-d', '--directory', type=click.Path(exists=True, resolve_path=True, file_okay=False, dir_okay=True, writable=True), help="temp directory")
@click.option('-t', '--targets', metavar='FILE', type=click.Path(exists=True, resolve_path=True, file_okay=True, dir_okay=False), help="target file")
@click.option('-v', '--verbose', count=True, help='the more times listed, the more output')
def bam2ec(bam_file, ec_file, chunks, targets, directory, verbose):
    """
    Convert a BAM file (bam_file) to an EC file (ec_file).
    """
    utils.configure_logging(verbose)
    alntools.bam2ec(bam_file, ec_file, chunks, targets, directory)


@cli.command('bam2emase', options_metavar='<options>', short_help='convert a BAM file to EC')
@click.argument('bam_file', metavar='bam_file', type=click.Path(exists=True, resolve_path=True, dir_okay=False))
@click.argument('emase_file', metavar='emase_file', type=click.Path(resolve_path=True, dir_okay=False, writable=True))
@click.option('-c', '--chunks', default=0, help="number of chunks to process")
@click.option('-d', '--directory', type=click.Path(exists=True, resolve_path=True, file_okay=False, dir_okay=True, writable=True), help="temp directory")
@click.option('-t', '--targets', metavar='FILE', type=click.Path(exists=True, resolve_path=True, file_okay=True, dir_okay=False), help="target file")
@click.option('-v', '--verbose', count=True, help='the more times listed, the more output')
def bam2emase(bam_file, emase_file, chunks, targets, directory, verbose):
    """
    Convert a BAM file (bam_file) to an EMASE file (emase_file).
    """
    utils.configure_logging(verbose)
    alntools.bam2emase(bam_file, emase_file, chunks, targets, directory)


@cli.command('emase2ec', options_metavar='<options>', short_help='convert an EMASE file to EC')
@click.argument('emase_file', metavar='emase_file', type=click.Path(resolve_path=True, dir_okay=False))
@click.argument('ec_file', metavar='ec_file', type=click.Path(resolve_path=True, dir_okay=False, writable=True))
@click.option('-v', '--verbose', count=True, help='the more times listed, the more output')
def emase2ec(emase_file, ec_file, verbose):
    """
    Convert an EMASE file (emase_file) to an EC file (ec_file).
    """
    utils.configure_logging(verbose)
    LOG = utils.get_logger()
    LOG.debug("EMASE: {}".format(emase_file))
    LOG.debug("EC: {}".format(ec_file))

    tstart = time.time()
    #create.create_snp_db(vcf_file, sqlite_file)
    tend = time.time()

    LOG.info("Creation time: {}".format(utils.format_time(tstart, tend)))


@cli.command('ec2emase', options_metavar='<options>', short_help='convert an EC file to EMASE')
@click.argument('ec_file', metavar='ec_file', type=click.Path(resolve_path=True, dir_okay=False))
@click.argument('emase_file', metavar='emase_file', type=click.Path(resolve_path=True, dir_okay=False, writable=True))
@click.option('-v', '--verbose', count=True, help='the more times listed, the more output')
def ec2emase(ec_file, emase_file, verbose):
    """
    Convert an EC file (ec_file) to an EMASE file (emase_file).
    """
    utils.configure_logging(verbose)
    LOG = utils.get_logger()
    LOG.debug("EC: {}".format(ec_file))
    LOG.debug("EMASE: {}".format(emase_file))

    tstart = time.time()
    #create.create_snp_db(vcf_file, sqlite_file)
    tend = time.time()

    LOG.info("Creation time: {}".format(utils.format_time(tstart, tend)))


if __name__ == '__main__':
    cli()

