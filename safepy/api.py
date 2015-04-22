# vim:ts=4:sw=4:softtabstop=4:smarttab:expandtab

# Copyright (C) 2014  Sangoma Technologies Corp.
# All Rights Reserved.
# Author(s)
# Simon Gomizelj <sgomizelj@sangoma.com>

import re
import keyword
from .url import url_builder, do_get, do_post
from .parser import parse


__all__ = ['api']


def make_typename(name):
    '''Sanitize a name to remove spaces and replace all instances of
    symbols which are not valid for python types with underscore. Should
    the name be None, return RestObject for compatibility. Eventually
    a strict more will raise an exception instead'''

    if name is None:
        return 'RestObject'

    name = name.encode('ascii', 'ignore')
    name = name.replace(' ', '')
    name = re.sub('[^a-zA-Z0-9_]', '_', name)

    if not all(c.isalnum() or c == '_' for c in name):
        raise ValueError('Type names and field names can only contain '
                         'alphanumeric characters and underscores: '
                         '{!r}'.format(name))
    if name[0].isdigit():
        raise ValueError('Type names and field names cannot start with '
                         'a number: {!r}'.format(name))
    if keyword.iskeyword(name):
        raise ValueError('Type names and field names cannot be a '
                         'keyword: {!r}'.format(name))

    return name


def make_docstring(description):
    '''Make a valid docstring. Needs to be handled carefully since
    sometimes the json specification returns an array instead of a list.'''

    if description is None:
        return None

    if isinstance(description, list):
        return '\n'.join(description)

    return description


# {{{ Make Functions
def make_list(url):
    def list(self, filter=None):
        response = do_get(url)

        # if no filter was provided, bail out now returning everything
        if filter is None:
            return response

        # otherwise only return entries who satisfy the filter
        matches = []
        for item in response:
            elem = self[item].retrieve()
            if all((k in elem and elem[k] == v)
                   for k, v in filter.iteritems()):
                matches.append(item)
        return matches
    return list


def make_create(ub):
    def create(self, key, data={}):
        ub(key).post('api', 'create', data)
        return self[key]
    return create


def make_retrieve(ub):
    def retrieve(self, key):
        return ub(key).get('api', 'retrieve')
    return retrieve


def make_update(ub):
    def update(self, key, data):
        ub(key).post('api', 'update', data)
    return update


def make_delete(ub):
    def delete(self, key):
        ub(key).post('api', 'delete')
    return delete


def make_getter(attr):
    def getter(self):
        return self.retrieve()[attr]
    return getter


def make_setter(attr):
    def setter(self, value):
        self.update({attr: value})
    return setter


def make_get_method(url):
    def get(self):
        return do_get(url)
    return get


def make_post_method(url):
    def post(self, data={}):
        do_post(url, data)
    return post


def make_getitem(node, ub):
    def __getitem__(self, key):
        return compile_child(node, ub(key))
    return __getitem__


def make_repr(fun):
    def __repr__(self):
        'x.__repr__() <==> repr(x)'
        return '{}({!r})'.format(self.__class__.__name__,
                                 getattr(self, fun)())
    return __repr__
# }}}


OVERRIDDEN = {'list': make_list}

COLLECTION_METHODS = {'create': make_create,
                      'retrieve': make_retrieve,
                      'update': make_update,
                      'delete': make_delete}

HTTP_REQUEST = {'GET': make_get_method,
                'POST': make_post_method}


def compile_methods(ast, ub, collection=False):
    '''Compile all the methods specified in the json 'methods' section.
    Prefer specialized implementations of common and important rest
    functions, falling back to a generic implementation for others.'''

    namespace = {}
    for node in ast:
        url = ub.url('api', node.tag)

        if node.tag in OVERRIDDEN:
            method = OVERRIDDEN[node.tag](url)
        elif collection and node.tag in COLLECTION_METHODS:
            method = COLLECTION_METHODS[node.tag](ub)
        else:
            request = node['request']
            method = HTTP_REQUEST[request](url)
            method.__name__ = make_typename(node.tag)

        method.__doc__ = make_docstring(node.get('description', None))
        namespace[method.__name__] = method

    return namespace


def compile_properties(ast):
    '''Compile all the attributes specified in the json 'class' section.
    Implemented as properties on the resulting class'''

    namespace = {}
    for node in ast:
        propname, docstring = make_typename(node.tag), node.get('help', None)
        namespace[propname] = property(make_getter(node.tag),
                                       make_setter(node.tag),
                                       None, docstring)

    return namespace


def object_template(node):
    typename = make_typename(node.get('name', None))
    docstring = make_docstring(node.get('description', None))

    return typename, {'__doc__': docstring}


def compile_child(node, ub):
    typename, namespace = object_template(node)

    namespace.update(compile_properties(node.cls))
    namespace.update(compile_objects(node.objs, ub))
    namespace.update(compile_methods(node.methods, ub))

    if 'retrieve' in namespace:
        namespace['__repr__'] = make_repr('retrieve')

    return type(typename, (), namespace)()


def compile_collection(node, ub):
    typename, namespace = object_template(node)

    namespace.update(compile_methods(node.methods, ub, True))
    namespace['__getitem__'] = make_getitem(node, ub)

    if 'list' in namespace:
        namespace['__repr__'] = make_repr('list')

    return type(typename, (), namespace)()


def compile_object(node, ub):
    '''Compile an object from the json specification. An object can be
    composed of methods and other objects. In the case that the object
    is not marked as a 'singleton', treat is like a collection and tack
    on a __getitem__ handler.'''

    if node.singleton:
        return compile_child(node, ub(node.tag))
    else:
        return compile_collection(node, ub(node.tag))


def compile_objects(ast, ub):
    return {n.tag: compile_object(n, ub) for n in ast}


def api(host, port=80, scheme='http'):
    '''Connects to a remote device, download the json specification
    describing the supported rest calls and dynamically compile a new
    object to wrap the rest.

    :param name: The hostname of the device to connect to.
    :type name: str
    :param port: The port the SAFe framework is listening on.
    :type port: int
    :param scheme: Specify the scheme of the request url.
    :type scheme: str
    :returns: the dynamically generated code.
    '''

    ub = url_builder(host, port, scheme)
    ast = parse(host, port, scheme)

    typename = make_typename(host.partition('.')[0].capitalize())
    namespace = compile_objects(ast, ub)

    product_cls = type(typename, (), namespace)
    return product_cls()
