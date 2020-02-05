import bz2
import gzip
import io
import json
import os
import re
from configparser import (ConfigParser, NoOptionError, NoSectionError,
                          ParsingError, RawConfigParser)

import numpy
from matplotlib import rcParams
from matplotlib.ticker import ScalarFormatter
from numpy import around
from numpy.core._multiarray_umath import fabs
from pandas import read_json
# to be used as a decorator for click commands
from pkg_resources import resource_filename

from cogent3 import DNA, load_table, make_table
from cogent3.core.alignment import ArrayAlignment
from cogent3.parse.fasta import MinimalFastaParser
from cogent3.util.union_dict import UnionDict

__author__ = "Gavin Huttley"
__copyright__ = "Copyright 2016, Gavin Huttley, Yicheng Zhu"
__credits__ = ["Gavin Huttley", "Yicheng Zhu"]
__license__ = "GPL"
__version__ = "0.3"
__maintainer__ = "Gavin Huttley"
__email__ = "Gavin.Huttley@anu.edu.au"
__status__ = "Development"

# to be used as a decorator for click commands
no_type3_font = click.option('--no_type3', is_flag=True,
                             help='Exclude Type 3 fonts from pdf, necessary'
                             ' for ScholarOne figures')


def exclude_type3_fonts():
    """stops matplotlib from using Type 3 fonts"""
    # this is because ScholarOne does not allow embedding of Type 3 fonts in
    # pdf's, a royal PITA. Thanks ScholarOne!!

    rcParams['pdf.fonttype'] = 42
    rcParams['ps.fonttype'] = 42


def get_plot_configs(cfg_path=None):
    """returns a config object with plotting settings"""
    defaults = dict(xlabel_fontsize=14, ylabel_fontsize=14,
                    xtick_fontsize=12, ytick_fontsize=12,
                    xlabel_pad=0.01, ylabel_pad=0.01)

    figwidths = {'1-way plot': 2.25, '2-way plot': 9, '3-way plot': 9,
                 '4-way plot': 9, 'summary plot': 9, 'grid': 8}
    figsizes = {'1-way plot': (9, 3), '2-way plot': (9, 9),
                '3-way plot': (9, 9), '4-way plot': (9, 9),
                'summary plot': (9, 9),
                'grid': (8, 8)}

    config = RawConfigParser()
    for section in ['1-way plot', '2-way plot', '3-way plot', '4-way plot',
                    'summary plot', 'grid']:
        config.add_section(section)
        config.set(section, 'figwidth', figwidths[section])
        config.set(section, 'figsize', figsizes[section])
        for arg, default in list(defaults.items()):
            config.set(section, arg, default)
    if cfg_path:
        # load the user provided config
        user_config = RawConfigParser(allow_no_value=True)
        try:
            user_config.read(cfg_path)
        except ParsingError as err:
            msg = 'Could not parse %s: %s' % (cfg_path, err)
            raise ParsingError(msg)

        # update the default settings
        for section in config.sections():
            for key, val in config.items(section):
                try:
                    new_val = user_config.get(section, key)
                    config.set(section, key, eval(new_val))
                except (NoSectionError, NoOptionError):
                    pass
    return config


# Code from Joe Kington's answer to
# http://stackoverflow.com/questions/3677368/matplotlib-format-axis-offset-values-to-whole-numbers-or-specific-number
class FixedOrderFormatter(ScalarFormatter):
    """Formats axis ticks using scientific notation with a constant order of
    magnitude"""

    def __init__(self, order_of_mag=0, useOffset=True, useMathText=False):
        self._order_of_mag = order_of_mag
        ScalarFormatter.__init__(self, useOffset=useOffset,
                                 useMathText=useMathText)

    def _set_orderOfMagnitude(self, range):
        """Over-riding this to avoid having orderOfMagnitude reset elsewhere"""
        self.orderOfMagnitude = self._order_of_mag


def load_table_from_delimited_file(path, sep='\t'):
    '''returns a Table object after a quicker loading'''
    with open_(path, 'rt') as infile:
        header = infile.readline().strip().split(sep)
        count_index = header.index('count')
        records = []
        for line in infile:
            line = line.strip().split(sep)
            line[count_index] = int(line[count_index])
            records.append(line)
        table = make_table(header=header, rows=records)
    return table


def spectra_table(table, group_label):
    """returns a table with columns without position information"""
    assert "direction" in table.header
    if "mut" in table.header:
        # remove redundant category (counts of M == R)
        table = table.filtered("mut=='M'")

    columns = ["count", "direction", group_label]
    table = table.get_columns(columns)
    # so we have a table with counts per direction
    results = []
    group_categories = table.distinct_values(group_label)
    filter_template = "direction=='%(direction)s' and " "%(label)s=='%(category)s'"
    for direction in table.distinct_values("direction"):
        start = direction[0]
        for group_category in group_categories:
            condition = dict(
                direction=direction, label=group_label, category=group_category
            )
            sub_table = table.filtered(filter_template % condition)
            total = sub_table.summed("count")
            results.append([total, start, direction, group_category])
    result = make_table(
        header=["count", "start", "direction", group_label], rows=results
    )
    return result


def get_subtables(table, group_label="direction"):
    """returns [(group, subtable),...] for distinct values of group_label"""
    groups = table.distinct_values(group_label)
    tables = []
    for group in groups:
        subtable = table.filtered(lambda x: x == group, columns=group_label)
        tables.append((group, subtable))
    return tables


def dump_loglin_stats(data, outfile_path):
    """save data in json format to outfile_path"""
    # convert all pandas data frames to json
    saveable = {}
    for position_set in data:
        curr = {}
        new_key = str(position_set)
        for key, value in list(data[position_set].items()):
            if key == "stats":
                value = data[position_set][key].to_json()
            curr[key] = value

        saveable[new_key] = curr

    with open(outfile_path, mode="w") as outfile:
        json.dump(saveable, outfile)


def load_loglin_stats(infile_path):
    """read in data in json format"""
    # convert all 'stats' to pandas data frames
    with open(infile_path) as infile:
        data = json.load(infile)

    new_data = {}
    for position_set in data:
        try:
            new_key = eval(position_set)
        except NameError:
            new_key = position_set

        new_data[new_key] = {}
        for key, value in list(data[position_set].items()):
            if key == "stats":
                value = read_json(value)
            new_data[new_key][key] = value
    return new_data


def is_valid(data):
    """returns True if all elements satisfy 0 <= e < 4"""
    return (data >= 0).all() and (data < 4).all()


def load_from_fasta(filename):
    infile = open_(filename, mode="rt")
    parser = MinimalFastaParser(infile)
    seqs = [(n, s) for n, s in parser]
    infile.close()
    return ArrayAlignment(data=seqs, moltype=DNA)


def array_to_str(data):
    """convert numpy array back to DNA sequence"""
    return ["".join(DNA.alphabet.from_indices(v)) for v in data]


def seqs_to_array(d_aln):
    """get input array alignment data, transfer them into a numpy array matrix,
    whith accordant numbers for DNA bases
    also
    filter sequences and save just_nuc sequences.
    """
    just_bases = just_nucs(d_aln.array_seqs)
    return just_bases


def just_nucs(seqs):
    """eliminate sequences containing gaps/Ns,
    along the 1st axis, match each base in seqs to <= 3 element-wise,
    give the indices of those just_nucs seq idx.
    """
    (indices,) = (seqs <= 3).all(axis=1).nonzero()
    just_bases = seqs.take(indices, axis=0)
    return just_bases


def open_(filename, mode="r"):
    """handles different compression"""
    op = {"gz": gzip.open, "bz2": bz2.BZ2File}.get(filename.split(".")[-1], open)
    return op(filename, mode)


def abspath(path):
    """returns an expanded, absolute path"""
    return os.path.abspath(os.path.expanduser(path))


def makedirs(path):
    """creates dir path"""
    try:
        os.makedirs(path)
    except OSError as e:
        pass
def get_config_parser(path, default):
    if not path or not os.path.exists(path):
        path = resource_filename("mutation_motif", f"cfgs/{default}")

    parser = ConfigParser()
    parser.optionxform = str  # stops automatic conversion to lower case
    if isinstance(path, io.TextIOBase):
        parser.read_file(path)
    else:
        parser.read(str(path))

    return parser


def get_fig_properties(parser, section="fig setup"):
    get_val = parser.get
    cfg = UnionDict()
    figsize = [
        100 * float(v.strip()) / 2.5 for v in get_val(section, "figsize").split(",")
    ]
    cfg.width, cfg.height = figsize

    try:
        margin = {
            k[0]: int(get_val(section, k)) for k in ("top", "bottom", "right", "left")
        }
        cfg.margin = margin
    except NoOptionError:
        pass

    # font sizes, title, label text padding
    for option in parser.options(section):
        valid = False
        for attr in ("pad", "font"):
            if attr in option:
                valid = True
                break
        if not valid:
            continue

        cfg[option] = int(get_val(section, option))

    # ylim
    try:
        ylim = float(get_val(section, "ylim"))
        cfg.ylim = ylim
    except NoOptionError:
        pass

    try:
        ylabel = get_val(section, "ylabel")
        cfg.ylabel = ylabel
    except NoOptionError:
        pass

    try:
        space = float(get_val(section, "space"))
        cfg.space = space
    except NoOptionError:
        pass

    try:
        xlabel = get_val(section, "xlabel")
        cfg.xlabel = xlabel
    except NoOptionError:
        pass

    for axis in ("rows", "cols"):
        key = f"num_{axis}"
        try:
            val = int(parser.get(section, key))
            cfg[key] = val
        except NoOptionError:
            pass

    return cfg


def get_spectra_config(path):
    parser = get_config_parser(path, default="spectra.cfg")
    col_title = parser.get("fig setup", "col_title")
    row_title = parser.get("fig setup", "row_title")
    cfg = get_fig_properties(parser, section="fig setup")
    cfg.col_title = col_title
    cfg.row_title = row_title
    return cfg


def get_nbr_config(path, section):
    parser = get_config_parser(path, default="nbr.cfg")
    cfg = get_fig_properties(parser, section=section)
    return cfg


def get_nbr_matrix_config(path):
    parser = get_config_parser(path, default="nbr_matrix.cfg")
    cfg = get_fig_properties(parser, section="fig setup")
    col_title = parser.get("fig setup", "col_title")
    row_title = parser.get("fig setup", "row_title")
    cfg.col_title = col_title
    cfg.row_title = row_title

    return cfg


def get_grid_config(path):
    dirname = None if path is None else os.path.dirname(path)
    parser = get_config_parser(path, default="grid.cfg")
    cfg = get_fig_properties(parser, section="fig setup")
    try:
        col_titles = [
            l.strip() for l in parser.get("fig setup", "col_titles").split(",")
        ]
        cfg.col_titles = col_titles
    except NoOptionError:
        pass

    try:
        row_titles = [
            l.strip() for l in parser.get("fig setup", "row_titles").split(",")
        ]
        cfg.row_titles = row_titles
    except NoOptionError:
        pass

    # load possible sections
    subplots = UnionDict()
    for section in parser.sections():
        try:
            coord = tuple(i - 1 for i in map(int, section.split(",")))
        except (TypeError, ValueError):
            continue

        try:
            path = parser.get(section, "path")
        except NoOptionError:
            continue

        if path is None:
            continue

        if dirname and dirname not in path:
            path = os.path.join(dirname, path)
        subplots[coord] = path

    if not subplots and path is not None:
        raise NoSectionError("no sections for subplots")

    cfg.paths = subplots

    return cfg


def get_summary_config(path):
    parser = get_config_parser(path, default="nbr.cfg")
    cfg = get_fig_properties(parser, section="summary")
    return cfg


def get_nbr_path_config(path):
    dirname = None if path is None else os.path.dirname(path)
    parser = get_config_parser(path, default="nbr_paths.cfg")
    cfg = UnionDict()
    for section in parser.sections():
        paths = {k: parser.get(section, k) for k in ("inpath", "outpath")}
        if not paths.get("inpath", None):
            continue

        if dirname and dirname not in paths["inpath"]:
            inpath = os.path.join(dirname, paths["inpath"])
            paths["inpath"] = inpath
            if section != "summary":
                assert inpath.endswith(".json"), f"{inpath} missing json suffix"

        outpath = paths.get("outpath", None)
        if not outpath:
            outpath = paths["inpath"].replace(".json", ".pdf")
        else:
            if dirname and dirname not in outpath:
                outpath = os.path.join(dirname, outpath)
        paths["outpath"] = outpath

        cfg[section] = UnionDict(paths)
    return cfg


