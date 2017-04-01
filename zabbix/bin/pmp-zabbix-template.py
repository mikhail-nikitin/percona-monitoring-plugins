#!/usr/bin/python
"""
This script creates Zabbix template and agent config from the existing
Perl definitions for Cacti and triggers if defined.

License: GPL License (see COPYING)
Copyright: 2013 Percona
Authors: Roman Vynar
"""
import dict2xml
import getopt
import re
import sys
import time
import yaml
from functools import wraps

VERSION = float("%d.%d" % (sys.version_info[0], sys.version_info[1]))
if VERSION < 2.6:
    sys.stderr.write("ERROR: python 2.6+ required. Your version %s is too ancient.\n" % VERSION)
    sys.exit(1)

# Constants
ZABBIX_VERSION = '3.0'
ZABBIX_SCRIPT_PATH = '/var/lib/zabbix/percona/scripts'
DEFINITION = 'cacti/definitions/mysql.def'
PHP_SCRIPT = 'cacti/scripts/ss_get_mysql_stats.php'
TRIGGERS = 'zabbix/triggers/mysql.yml'
EXTRA_ITEMS = 'zabbix/items/mysql.yml'
EXTRA_ITEM_UPDATE_INTERVAL = 10
ITEM_UPDATE_INTERVAL = 10
PING_INTERVAL = 5
ITEM_KEEP_HISTORY_DAYS = 90
ITEM_KEEP_TRENDS_DAYS = 365
DISCOVERY_RULE_DELAY = 10

CATEGORY_HELPER_FIELD = 'category'
DO_NOT_CONVERT_TO_TRAPPER_HELPER_FIELD = 'do_not_convert_to_trapper'

COMMON_CATEGORY = 'common'
SLAVE_CATEGORY = 'slave'
QUERY_COUNTER_CATEGORY = 'query_counter'
WSREP_CATEGORY = 'wsrep'

CATEGORY_KEYWORDS = {SLAVE_CATEGORY: 'slave',
                     QUERY_COUNTER_CATEGORY: 'query-time',
                     WSREP_CATEGORY: 'wsrep'}

DISCOVERY_RULES = [{'key': 'instances[]', 'name': 'MySQL Instances', 'category': COMMON_CATEGORY},
                   {'key': 'instances[slaves]', 'name': 'MySQL Slave instances', 'category': SLAVE_CATEGORY},
                   {'key': 'instances[with_query_counter]', 'name': 'MySQL Instances with query counter', 'category': QUERY_COUNTER_CATEGORY},
                   {'key': 'instances[wsrep]', 'name': 'MySQL Galera instances', 'category': WSREP_CATEGORY}]
ALL_ITEM_CATEGORIES = [rule['category'] for rule in DISCOVERY_RULES]

item_types = {'Zabbix agent': 0,
              'Zabbix agent (active)': 7,
              'Simple check': 3,
              'SNMPv1': 1,
              'SNMPv2': 4,
              'SNMPv3': 6,
              'SNMP Trap': 17,
              'Zabbix Internal': 5,
              'Zabbix Trapper': 2,
              'Zabbix Aggregate': 8,
              'External check ': 10,
              'Database monitor': 11,
              'IPMI agent': 12,
              'SSH agent': 13,
              'TELNET agent': 14,
              'JMX agent': 16,
              'Calculated': 15,
              'snmp_community': '',}

item_value_types = {'Numeric (unsigned)': 3,
                    'Numeric (float)': 0,
                    'Character': 1,
                    'Log': 2,
                    'Text': 4}

item_data_type = {'decimal': 0,
                  'octal': 1,
                  'hexadecimal': 2,
                  'boolean': 3}

# Cacti to Zabbix relation
item_store_values = {1: 0,  # GAUGE == As is
                     2: 1,  # COUNTER == Delta (speed per second)
                     3: 1}  # DERIVE == Delta (speed per second)
# Others: Delta (simple change) 2
item_value_storage_type = {'As is': 0,
                           'Delta (speed per second)': 1,
                           'Delta (simple change)': 2}

graph_types = {'Normal': 0,
               'Stacked': 1,
               'Pie': 2,
               'Exploded': 3}

graph_item_functions = {'all': 7,
                        'min': 1,
                        'avg': 2,
                        'max': 4}

# Cacti to Zabbix relation
graph_item_draw_styles = {'LINE1': 0,  # Line
                          'LINE2': 2,  # Bold line
                          'AREA': 1,  # Filled region
                          'STACK': 0}  # Line
# Others: Dot 3, Dashed line 4, Gradient line 5

graph_y_axis_sides = {'Left': 0,
                      'Right': 1}

trigger_severities = {'Not_classified ': 0,
                      'Information': 1,
                      'Warning': 2,
                      'Average': 3,
                      'High': 4,
                      'Disaster': 5}

# Parse args
usage = """
    -h, --help                        Prints this menu and exits
    -o, --output [xml|config|xml-lld] Type of the output, default - xml. Xml-lld is for low-level item discovery
"""
try:
    opts, args = getopt.getopt(sys.argv[1:], "ho:v", ["help", "output="])
except getopt.GetoptError as err:
    sys.stderr.write('%s\n%s' % (err, usage))
    sys.exit(2)
# Defaults
output = 'xml'
verbose = False
for o, a in opts:
    if o == "-v":
        verbose = True
    elif o in ("-h", "--help"):
        print usage
        sys.exit()
    elif o in ("-o", "--output"):
        output = a
        if output not in ['xml', 'config', 'xml-lld']:
            sys.stderr.write('invalid output type\n%s' % usage)
            sys.exit(2)
    else:
        assert False, "unhandled option"

# Read Cacti template definition file and load as YAML
dfile = open(DEFINITION, 'r')
data = []
for line in dfile.readlines():
    if not line.strip().startswith('#'):
        data.append(line.replace('=>', ':'))
data = yaml.safe_load(' '.join(data))

# Define the base of Zabbix template
tmpl = dict()
app_name = data['name'].split()[0]
tmpl_name = 'Percona %s Template' % data['name']
tmpl['version'] = ZABBIX_VERSION
tmpl['date'] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
tmpl['groups'] = {'group': {'name': 'Percona Templates'}}
tmpl['screens'] = {'screen': {'name': '%s Graphs' % app_name,
                              'hsize': 2,
                              'vsize': int(round(len(data['graphs']) / 2.0)),
                              'screen_items': {'screen_item': []}}}
tmpl['templates'] = {'template': {'template': tmpl_name,
                                  'name': tmpl_name,
                                  'description': tmpl_name,
                                  'groups': tmpl['groups'],
                                  'applications': {'application': {'name': app_name}},
                                  'items': {'item': []},
                                  'macros': ''}}
tmpl['graphs'] = {'graph': []}
tmpl['triggers'] = ''


def remove_duplicate_keys(items):
    dictionary = dict([(item['key'], item) for item in items])
    return dictionary.values()


def index_items_by_category(items):
    result = {}

    for category in ALL_ITEM_CATEGORIES:
        result[category] = []

    for item in items:
        category = COMMON_CATEGORY
        if CATEGORY_HELPER_FIELD in item:
            category = item[CATEGORY_HELPER_FIELD]

        if category not in result:
            result[category] = []

        result[category].append(item)
    return result


def categorize_items(items):
    return [categorize_single_item(item) for item in items]


def categorize_single_item(item):
    if CATEGORY_HELPER_FIELD in item:
        return item
    for category, keyword in CATEGORY_KEYWORDS.items():
        if get_keyword_matching_regex(keyword).match(item['key']):
            item[CATEGORY_HELPER_FIELD] = category
            break
    return item


def single_argument_memoize(f):
    cache = {}

    @wraps(f)
    def wrapper(arg):
        if arg in cache:
            result = cache[arg]
        else:
            result = f(arg)
            cache[arg] = result
        return result
    return wrapper


@single_argument_memoize
def get_keyword_matching_regex(keyword):
    return re.compile(re.escape(format_item(keyword) + '-'), re.IGNORECASE)


def remove_helper_fields_from_items(items):
    return [remove_helper_fields_from_single_item(item) for item in items]


def remove_helper_fields_from_single_item(item):
    if CATEGORY_HELPER_FIELD in item:
        item.pop(CATEGORY_HELPER_FIELD)
    if DO_NOT_CONVERT_TO_TRAPPER_HELPER_FIELD in item:
        item.pop(DO_NOT_CONVERT_TO_TRAPPER_HELPER_FIELD)
    return item


def convert_items_to_trapper_prototypes(items):
    return [convert_single_item_to_trapper_prototype(item) for item in items]


def convert_item_to_prototype(item):
    item['name'] += ' {#MYSQL_INSTANCE_NAME}'
    if item['key'].find('{#MYSQL_INSTANCE}') == -1:
        item['key'] += '[{#MYSQL_INSTANCE}]'
    item['application_prototypes'] = {}
    return item


def convert_single_item_to_trapper_prototype(item):
    item = convert_item_to_prototype(item)
    if DO_NOT_CONVERT_TO_TRAPPER_HELPER_FIELD not in item or not item[DO_NOT_CONVERT_TO_TRAPPER_HELPER_FIELD]:
        item['type'] = item_types['Zabbix Trapper']
        item['delay'] = 0
    return item


def create_discovery_rule(name, key, rule={}):
    result = {'name': name,
              'type': '0',
              'snmp_community': '',
              'snmp_oid': '',
              'key': format_item(key),
              'delay': DISCOVERY_RULE_DELAY,
              'status': '0',
              'allowed_hosts': '',
              'snmpv3_contextname': '',
              'snmpv3_securityname': '',
              'snmpv3_securitylevel': '0',
              'snmpv3_authprotocol': '0',
              'snmpv3_authpassphrase': '',
              'snmpv3_privprotocol': '0',
              'snmpv3_privpassphrase': '',
              'delay_flex': '',
              'params': '',
              'ipmi_sensor': '',
              'authtype': '0',
              'username': '',
              'password': '',
              'publickey': '',
              'privatekey': '',
              'port': '',
              'filter': {'evaltype': 0,
                         'formula': '',
                         'conditions': {}},
              'lifetime': '1',
              'description': '',
              'item_prototypes': {},
              'trigger_prototypes': {},
              'graph_prototypes': {},
              'host_prototypes': {}}
    result.update(rule)
    return result


def create_item(key, name, value_type,
                type=item_types['Zabbix agent'],
                data_type=item_data_type['decimal'],
                value_storage_type=item_value_storage_type['As is'],
                unit='',
                multiplication_factor=None,
                update_interval=ITEM_UPDATE_INTERVAL,
                history=ITEM_KEEP_HISTORY_DAYS,
                trends=ITEM_KEEP_TRENDS_DAYS):
    result = {'name': name,
              'type': type,
              'key': key,
              'value_type': value_type,
              'data_type': data_type,  # Decimal the above is Numeric (unsigned)
              'units': unit,
              'delay': update_interval,  # Update interval (in sec)
              'history': history,
              'trends': trends,
              'delta': value_storage_type,
              'applications': {'application': {'name': app_name}},
              'description': '%s %s' % (app_name, name),
              'multiplier': 1 if multiplication_factor is not None else 0,
              'formula': multiplication_factor if multiplication_factor is not None else 1,
              'status': 0,
              'snmp_community': '',
              'snmpv3_contextname': '',
              'snmpv3_securityname': '',
              'snmpv3_securitylevel': 0,
              'snmpv3_authprotocol': 0,
              'snmpv3_authpassphrase': '',
              'snmpv3_privpassphrase': '',
              'snmpv3_privprotocol': 0,
              'snmp_oid': '',
              'delay_flex': '',
              'params': '',
              'ipmi_sensor': '',
              'authtype': 0,
              'username': '',
              'password': '',
              'publickey': '',
              'privatekey': '',
              'port': '',
              'inventory_link': 0,
              'valuemap': '',
              'logtimefmt': '',
              'allowed_hosts': '',
              }
    return result


def format_item(f_item):
    """Underscore makes an agent to throw away the support for item
    """
    return '%s.%s' % (app_name, f_item.replace('_', '-'))


def load_extra_items():
    result = []
    try:
        f = open(EXTRA_ITEMS, 'r')
        items = yaml.safe_load(f)
        for item in items:
            result_item = create_item(name=item['name'],
                                      key=get_key_for_extra_item(item),
                                      value_type=get_value_type_for_extra_item(item),
                                      value_storage_type=item_value_storage_type[item['value_storage_type']] if 'value_storage_type' in item else item_value_storage_type['As is'],
                                      data_type=get_data_type_for_extra_item(item),
                                      update_interval=item['update_interval'] if 'update_interval' in item else EXTRA_ITEM_UPDATE_INTERVAL,
                                      unit=item['unit'] if 'unit' in item else '')
            if DO_NOT_CONVERT_TO_TRAPPER_HELPER_FIELD in item:
                result_item[DO_NOT_CONVERT_TO_TRAPPER_HELPER_FIELD] = item[DO_NOT_CONVERT_TO_TRAPPER_HELPER_FIELD]
            result.append(result_item)
        f.close()
    except IOError:
        result = []
    return result


def get_key_for_extra_item(item):
    key = format_item(item['key']) if item['key'].find('.') == -1 else item['key']
    if 'prototype_suffix' in item:
        key += item['prototype_suffix']
    return key


def get_value_type_for_extra_item(item):
    if 'is_text' in item and item['is_text']:
        return item_value_types['Text']
    if ('is_unsigned' in item and item['is_unsigned']) or \
       ('is_bool' in item and item['is_bool']):
        return item_value_types['Numeric (unsigned)']
    return item_value_types['Numeric (float)']


def get_data_type_for_extra_item(item):
    if 'is_bool' in item and item['is_bool']:
        return item_data_type['boolean']
    return item_data_type['decimal']


# Parse definition
all_item_keys = set()
x = y = 0
for graph in data['graphs']:
    # Populate graph
    z_graph = {'name': graph['name'],
               'width': 900,
               'height': 200,
               # 'graphtype': graph_types['Normal'], #commented out to work with Zabbix3
               'type': graph_types['Normal'],
               'show_legend': 1,
               'show_work_period': 1,
               'show_triggers': 1,
               'yaxismin': 0,
               'yaxismax': 0,
               'ymin_item_1': 0,
               'ymax_item_1': 0,
               'ymin_type_1': 0,
               'ymax_type_1': 0,
               'show_3d': 0,
               'percent_left': '0.00',
               'percent_right': '0.00',
               'graph_items': {'graph_item': []}}

    # Populate graph items
    multipliers = dict()
    i = 0
    for item in graph['items']:
        if item not in ['hash', 'task']:
            draw_type = item['type']
            if draw_type not in graph_item_draw_styles.keys():
                sys.stderr.write("ERROR: Cacti graph item type %s is not supported for item %s.\n" % (draw_type, item['item']))
                sys.exit(1)
            cdef = item.get('cdef')
            if cdef == 'Negate':
                multipliers[item['item']] = (1, -1)
            elif cdef == 'Turn Into Bits':
                multipliers[item['item']] = (1, 8)
            elif cdef:
                sys.stderr.write("ERROR: CDEF %s is not supported for item %s.\n" % (cdef, item['item']))
                sys.exit(1)
            else:
                multipliers[item['item']] = (0, 1)
            z_graph_item = {'item': {'key': format_item(item['item']),
                                     'host': tmpl_name},
                            'calc_fnc': graph_item_functions['avg'],
                            'drawtype': graph_item_draw_styles[draw_type],
                            'yaxisside': graph_y_axis_sides['Left'],
                            'color': item['color'],
                            'sortorder': i,
                            'type': 0}
            z_graph['graph_items']['graph_item'].append(z_graph_item)
            i = i + 1
    tmpl['graphs']['graph'].append(z_graph)

    # Add graph to the screen
    z_screen_item = {'resourcetype': 0,  # Graph
                     'width': 500,
                     'height': 120,
                     'valign': 1,  # Middle
                     'halign': 0,  # Center
                     'colspan': 1,
                     'rowspan': 1,
                     'x': x,
                     'y': y,
                     'dynamic': 1,
                     'style': 0,
                     'elements': 25,
                     'sort_triggers': 0,
                     'url': '',
                     'application': '',
                     'max_columns': 3,
                     'resource': {'name': graph['name'],
                                  'host': tmpl_name}}
    tmpl['screens']['screen']['screen_items']['screen_item'].append(z_screen_item)
    tmpl['templates']['template']['screens'] = tmpl['screens']
    if x == 0:
        x = 1
    else:
        x = 0
        y = y + 1

    # Populate items
    for item in graph['dt'].keys():
        if item not in ['hash', 'input']:
            ds_type = int(graph['dt'][item]['data_source_type_id'])
            if ds_type == 4:
                sys.stderr.write("ERROR: Cacti DS type ABSOLUTE is not supported for item %s.\n" % item)
                sys.exit(1)
            name = item.replace('_', ' ').title()
            name = re.sub(r'^[A-Z]{4,} ', '', name)
            base_value = int(graph['base_value'])
            if base_value == 1000:
                unit = ''
            elif base_value == 1024:
                unit = 'B'
            else:
                sys.stderr.write("ERROR: base_value %s is not supported for item %s.\n" % (base_value, item))
                sys.exit(1)
            key = format_item(item)
            z_item = create_item(key=key,
                                 type=item_types['Zabbix agent'],
                                 name=name,
                                 value_type=item_value_types['Numeric (float)'],
                                 data_type=item_data_type['decimal'],
                                 value_storage_type=item_store_values[ds_type],
                                 multiplication_factor=multipliers[item][1] if item in multipliers and multipliers[item][0] > 0 else None,
                                 unit=unit)
            tmpl['templates']['template']['items']['item'].append(z_item)
            all_item_keys.add(item)


def print_xml(template_definition):
    # Convert and write XML
    xml = dict2xml.Converter(wrap='zabbix_export', indent='  ').build(template_definition)
    print '<?xml version="1.0" encoding="UTF-8"?>\n%s' % xml


# Generate output
if output == 'xml':
    # Add extra items required by triggers
    extra_items = [{'name': 'Total number of mysqld processes',
                    'key': 'proc.num[mysqld]'},
                   {'name': 'MySQL running slave',
                    'key': format_item('running-slave')}]
    for item in extra_items:
        z_item = create_item(key=item['key'], name=item['name'], update_interval=EXTRA_ITEM_UPDATE_INTERVAL)
        tmpl['templates']['template']['items']['item'].append(z_item)

    # Read triggers from YAML file
    dfile = open(TRIGGERS, 'r')
    data = yaml.safe_load(dfile)

    # Populate triggers
    trigger_refs = dict((t['name'], t['expression'].replace('TEMPLATE', tmpl_name)) for t in data)
    if trigger_refs:
        tmpl['triggers'] = {'trigger': []}
    for trigger in data:
        z_trigger = {'name': trigger['name'],
                     'expression': trigger['expression'].replace('TEMPLATE', tmpl_name),
                     'priority': trigger_severities[trigger.get('severity', 'Not_classified')],
                     'status': 0,  # Enabled
                     'dependencies': '',
                     'url': '',
                     'description': '',
                     'type': item_types['Zabbix agent'],
                     }
        # Populate trigger dependencies
        if trigger.get('dependencies'):
            z_trigger['dependencies'] = {'dependency': []}
            for dep in trigger['dependencies']:
                exp = trigger_refs.get(dep)
                if not exp:
                    sys.stderr.write("ERROR: Dependency trigger '%s' is not defined for trigger '%s'.\n" % (dep, trigger['name']))
                    sys.exit(1)
                z_trigger_dep = {'name': dep,
                                 'expression': exp}
                z_trigger['dependencies']['dependency'].append(z_trigger_dep)
        tmpl['triggers']['trigger'].append(z_trigger)

    print_xml(tmpl)

elif output == 'xml-lld':
    items = tmpl['templates']['template']['items']['item']
    items.extend(load_extra_items())
    items = convert_items_to_trapper_prototypes(items)
    items = remove_duplicate_keys(items)
    items = categorize_items(items)
    items_by_category = index_items_by_category(items)

    tmpl['templates']['template']['items'] = {}
    tmpl['graphs'] = {}
    tmpl['triggers'] = {}
    tmpl['templates']['template']['screens'] = {}
    tmpl['screens'] = {}

    rules = []
    for rule_definition in DISCOVERY_RULES:
        item_prototypes = items_by_category[rule_definition['category']]
        item_prototypes = remove_helper_fields_from_items(item_prototypes)
        if len(item_prototypes) > 0:
            rule = create_discovery_rule(name=rule_definition['name'], key=rule_definition['key'])
            rule['item_prototypes'] = {'item_prototype': item_prototypes}
            rules.append(rule)
    tmpl['templates']['template']['discovery_rules'] = {'discovery_rule': rules}

    print_xml(tmpl)

elif output == 'config':
    # Read Perl hash aka MAGIC_VARS_DEFINITIONS from Cacti PHP script
    dfile = open(PHP_SCRIPT, 'r')
    data = []
    store = 0
    for line in dfile.readlines():
        line = line.strip()
        if not line.startswith('#'):
            if store == 1:
                if line == ');':
                    break
                data.append(line.replace('=>', ':'))
            elif line == '$keys = array(':
                store = 1
    data = yaml.safe_load('{%s}' % ' '.join(data))

    # Write Zabbix agent config
    for item in all_item_keys:
        print "UserParameter=%s,%s/get_mysql_stats_wrapper.sh %s" % (format_item(item), ZABBIX_SCRIPT_PATH, data[item])

    # Write extra items
    print "UserParameter=%s,%s/get_mysql_stats_wrapper.sh running-slave" % (format_item('running-slave'), ZABBIX_SCRIPT_PATH)
