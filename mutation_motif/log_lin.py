from rpy2.robjects import Formula, r as R
from rpy2.robjects.vectors import DataFrame, StrVector, IntVector, FactorVector

import pandas as pd

import numpy

def as_dataframe(table):
    '''returns a DataFrame instance. Requires counts to be [[col1, col2, col3, ..]]'''
    data = dict(zip(table.Header, zip(*table.getRawData())))
    for column in data:
        if type(data[column][0]) in (unicode, str):
            klass = StrVector
        else:
            klass = IntVector
        
        data[column] = klass(data[column])
        
    return DataFrame(data)

def get_rpy2_factorvector_index_map(fac):
    '''returns dict mapping factor vector indices to factors'''
    mapping = dict([(i+1, f) for i, f in enumerate(fac.levels)])
    return mapping

def convert_rdf_to_pandasdf(r_df):
    '''converts an rpy2 dataframe to a pandas dataframe'''
    converted = {}
    for col_name, col_vector in r_df.items():
        if type(col_vector) == FactorVector:
            index_factor_map = get_rpy2_factorvector_index_map(col_vector)
            col_vector = [index_factor_map[val] for val in col_vector]
        converted[col_name] = list(col_vector)
    return pd.DataFrame(converted)

def DevianceToRelativeEntropy(N):
    '''converts deviance to Relative Entropy'''
    denom = 2 * N
    def call(val):
        return val / denom
    return call

def CalcRet(dev_to_re, epsilon=1e-9):
    '''factory function for computing residual relative entropy terms.
    
    dev_to_re is a function for converting a deviance to relative entropy'''
    def call(obs, exp):
        result = []
        for i in range(len(obs)):
            o, e = obs[i], exp[i]
            e = e or epsilon # to avoide zero division
            o = o or epsilon # avoid zero counts
            ret = dev_to_re(2 * o * numpy.log(o / e))
            result.append(ret)
        return result
    return call

def position_effect(counts_table, test=False):
    """returns total relative entropy, degrees of freedom and stats
    
    fit's a log-lin model that is excludes only the full interaction term"""
    num_pos = sum(1 for c in counts_table.Header if c.startswith('base'))
    assert 1 <= num_pos <= 4, "Can only handle 4 positions"
    
    if num_pos == 1:
        columns = ['mut', 'base', 'count']
    else:
        columns = ['mut'] + ['base%d' % (i + 1) for i in range(num_pos)] + ['count']
    
    factors = columns[:-1]
    formula = " - ".join([" * ".join(factors), " : ".join(factors)])
    formula = "count ~ %s" % formula
    null = Formula(formula)
    if test:
        print formula
    
    counts_table = counts_table.getColumns(columns)
    d = as_dataframe(counts_table)
    
    f = R.glm(null, data=d, family = "poisson")
    f_attr = dict(f.items())
    dev = f_attr['deviance'][0]
    df = f_attr['df.null'][0]
    
    collated = convert_rdf_to_pandasdf(f_attr['data'])
    collated['fitted'] = list(f_attr['fitted.values'])
    dev_to_re = DevianceToRelativeEntropy(collated['count'].sum())
    calc_ret = CalcRet(dev_to_re)
    total_re = dev_to_re(dev)
    
    collated['ret'] = calc_ret(collated['count'], collated['fitted'])
    collated = collated.reindex_axis(columns + ['fitted', 'ret'], axis=1)
    collated = collated.sort(columns=columns[:-1])
    return total_re, dev, df, collated, formula
    