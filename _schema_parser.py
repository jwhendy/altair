"""
This file contains utilities to parse the vegalite schema and write low-level
Python wrappers into the altair source directory.
"""
import json
import os


class SchemaDefinition(object):
    """Base class for representing a vegalite schema item as a Python class"""
    file_comment = ("# This file auto-generated by `_schema_parser.py`.\n"
                    "# Do not modify this file directly.")
    test_template = ("from ... import {name}\n\n\n"
                     "def test_{name}():\n"
                     "    obj = {name}()\n")

    @classmethod
    def init(cls, name, schema):
        typ = schema['definitions'][name]['type']
        if typ == 'object':
            return ObjectDefinition(name, schema)
        elif typ == 'string':
            return StringDefinition(name, schema)
        else:
            raise NotImplementedError("Definition type='{0}'".format(typ))

    @staticmethod
    def format_help_string(help_str, summarize=True):
        """Format and optionally summarize a help string"""
        if summarize:
            help_str = help_str.split('(e.g.')[0].split('.')[0]
        return '"""{0}."""'.format(help_str.rstrip())

    @staticmethod
    def kwds_to_str(kwds):
        """Convert a dictionary of keywords to a string of keyword arguments"""
        vals = ', '.join("{0}={1}".format(key, val)
                         for key, val in sorted(kwds.items())
                         if key != 'help')
        if 'help' in kwds:
            vals += ', help={0}'.format(kwds['help'])
        return vals

    def get_attr_kwds(self, attr_dict):
        kwds = {'allow_none': 'True',
                'default_value': 'None'}
        if 'description' in attr_dict:
            kwds['help'] = self.format_help_string(attr_dict['description'],
                                                   summarize=True)
        if 'minimum' in attr_dict:
            kwds['min'] = attr_dict['minimum']
        if 'maximum' in attr_dict:
            kwds['max'] = attr_dict['maximum']
        return kwds

    def class_definition(self):
        """Create the class definition for the Python wrapper"""
        code = self.code()
        # imports now populated; prepend them to the code
        imports = '\n'.join(self.imports)
        return "{0}\n\n{1}\n\n\n{2}\n".format(self.file_comment, imports, code)

    def test_script(self):
        """Create the test script for the Python wrapper"""
        return "{0}\n\n{1}".format(self.file_comment,
                                   self.test_template.format(name=self.name))

    def code(self):
        """Return the code representing the object"""
        raise NotImplementedError()


class StringDefinition(SchemaDefinition):
    """Wrapper for 'string'-type definitions"""
    enum_template = """class {cls}(T.Enum):
    def __init__(self, default_value=T.Undefined, **metadata):
        super({cls}, self).__init__({values},
                                    default_value=default_value,
                                    **metadata)"""
    string_template = """class {cls}(T.Unicode):
    pass"""

    def __init__(self, name, schema):
        self.name = name
        self.schema = schema
        self.definition = schema['definitions'][name]
        self.imports = ['import traitlets as T']

    def code(self):
        """Return the code representing the object"""
        if 'enum' in self.definition:
            return self.enum_template.format(cls=self.name,
                                             values=self.definition['enum'])
        else:
            return self.string_template.format(cls=self.name)


class ObjectDefinition(SchemaDefinition):
    """Wrapper for 'object'-type definitions"""
    class_template = """class {name}(BaseObject):\n"""
    attr_template = """    {0} = {1}\n"""

    def __init__(self, name, schema):
        self.name = name
        self.schema = schema
        self.definition = schema['definitions'][name]
        self.prop_dict = self.definition['properties']
        self.imports = ['import traitlets as T',
                        'from ..baseobject import BaseObject']

    def code(self):
        """Return the code representing the object"""
        code = self.class_template.format(name=self.name)
        code += ''.join(self.attr_template.format(key, self.any_attribute(val))
                       for key, val in sorted(self.prop_dict.items()))
        return code

    def instance_str(self, cls, *args, **kwds):
        """Create a traitlet construction string"""
        if args:
            args = ', '.join(sorted(args)) + ', '
        else:
            args = ''
        return "T.{0}({1}{2})".format(cls, args, self.kwds_to_str(kwds))

    def any_attribute(self, attr_dict):
        """Switch-statement for representing attributes as strings"""
        if 'type' in attr_dict:
            return self.type_attribute(attr_dict)
        elif '$ref' in attr_dict:
            return self.ref_attribute(attr_dict)
        elif 'oneOf' in attr_dict:
            return self.oneof_attribute(attr_dict)
        else:
            raise NotImplementedError('unrecognized attribute keys')

    def type_attribute(self, attr_dict):
        """Represent a 'type'-attribute as a string"""
        kwds = self.get_attr_kwds(attr_dict)
        tp = attr_dict["type"]

        if tp == "array":
            typename = self.any_attribute(attr_dict['items'])
            return self.instance_str("List", typename, **kwds)
        elif tp == "boolean":
            return self.instance_str("Bool", **kwds)
        elif tp == "number":
            return self.instance_str("CFloat", **kwds)
        elif tp == "string":
            return self.instance_str("Unicode", **kwds)
        elif tp == "object":
            return self.instance_str("Any", **kwds)
        else:
            raise NotImplementedError(tp)

    def ref_attribute(self, attr_dict):
        """Represent a '$ref'-attribute as a string"""
        kwds = self.get_attr_kwds(attr_dict)
        _, _, name = attr_dict['$ref'].split('/')

        self.imports.append('from .{0} import {1}'
                            ''.format(name.lower(), name))

        reftype = self.schema['definitions'][name]['type']
        if reftype == 'object':
            return self.instance_str("Instance", name, **kwds)
        elif reftype == 'string':
            return "{0}({1})".format(name, self.kwds_to_str(kwds))
        else:
            raise NotImplementedError("type = '{0}'".format(reftype))

    def oneof_attribute(self, attr_dict):
        """Represent a 'oneOf'-attribute as a string"""
        types = (self.any_attribute(attr) for attr in attr_dict['oneOf'])
        return "T.Union([{0}])".format(', '.join(sorted(types)))


class VegaLiteSchema(object):
    """
    This is a wrapper for the vegalite JSON schema that provides tools to
    export Python wrappers.
    """
    def __init__(self, schema_file=None):
        if schema_file is None:
            schema_file = os.path.join(os.path.dirname(__file__),
                                       'altair', 'schema',
                                       'vega-lite-schema.json')
        self.schema_file = schema_file
        self.schema = self.read_schema()

    def read_schema(self):
        print(" > reading vegalite schema from {0}".format(self.schema_file))
        with open(self.schema_file) as f:
            return json.load(f)

    def definitions(self):
        """Iterator over schema definitions"""
        for name in sorted(self.schema['definitions']):
            yield name, SchemaDefinition.init(name, self.schema)

    def write_python_wrappers(self, codedir=None):
        """Write Python wrappers for the schema"""
        if codedir is None:
            codedir = os.path.join(os.path.dirname(__file__),
                                   'altair', 'schema', '_generated')
        testdir = os.path.join(codedir, 'tests')
        print(" > writing Python wrappers to {0}".format(codedir))

        if not os.path.exists(testdir):
            os.makedirs(testdir)

        with open(os.path.join(testdir, '__init__.py'), 'w') as initfile:
            initfile.write('"""Auto-generated unit tests '
                           'wrappers for vegalite schema"""\n')


        with open(os.path.join(codedir, '__init__.py'), 'w') as initfile:
            initfile.write('"""Auto-generated Python '
                           'wrappers for vegalite schema"""\n')

            for name, obj in self.definitions():
                module = name.lower()

                codepath = os.path.join(codedir, '{0}.py'.format(module))
                with open(codepath, 'w') as codefile:
                    codefile.write(obj.class_definition())

                testpath = os.path.join(testdir, 'test_{0}.py'.format(module))
                with open(testpath, 'w') as testfile:
                    testfile.write(obj.test_script())

                initfile.write("\nfrom .{0} import {1}".format(module, name))
        print(" > success!")


if __name__ == '__main__':
    VegaLiteSchema().write_python_wrappers()
