import inspect
from importlib import import_module, reload


class GenNodeCode:
    """Generates A Python Class for a given SECoP_Node_Device instance. This allows
    autocompletiion and type hinting in IDEs, this is needed since the attributes of
    the generated Ophyd devices are only known at runtime.
    """

    ModName: str = "genNodeClass"
    node_mod = None

    def __init__(self):
        """Instantiates GenNodeCode, internally all atrribues on a node and module level
        are collected. Additionally all the needed imports are collected in a dict
        """
        # prevent circular import
        from secop_ophyd.SECoPDevices import (
            SECoP_Node_Device,
            SECoPMoveableDevice,
            SECoPReadableDevice,
            SECoPWritableDevice,
        )

        self.dimport: dict[str, set[str]] = {}
        self.dmodule: dict = {}
        self.dnode: dict = {}

        self.import_string: str = ""
        self.mod_cls_string: str = ""
        self.node_cls_string: str = ""

        try:
            self.node_mod = import_module(self.ModName)
        except ModuleNotFoundError:
            print("no code generated yet, building from scratch")
            return

        modules = inspect.getmembers(self.node_mod)

        results = filter(lambda m: inspect.isclass(m[1]), modules)
        for class_symbol, class_obj in results:
            module = class_obj.__module__

            if module == self.ModName:
                # Node Classes
                if issubclass(class_obj, SECoP_Node_Device):

                    source = inspect.getsource(class_obj).split("\n")
                    # remove first line
                    source.pop(0)

                    bases = [base.__name__ for base in class_obj.__bases__]

                    # remove whitespace
                    source = [attr.replace(" ", "") for attr in source]
                    # split at colon
                    attrs = [tuple(attr.split(":")) for attr in source]
                    attrs.pop()

                    self.add_node_class(class_symbol, bases, attrs)
                    continue

                if issubclass(
                    class_obj,
                    (SECoPMoveableDevice, SECoPReadableDevice, SECoPWritableDevice),
                ):

                    source = inspect.getsource(class_obj).split("\n")
                    # remove first line
                    source.pop(0)

                    bases = [base.__name__ for base in class_obj.__bases__]

                    # remove whitespace
                    source = [attr.replace(" ", "") for attr in source]
                    # split at colon
                    attrs = [tuple(attr.split(":")) for attr in source]
                    attrs.pop()

                    self.add_mod_class(class_symbol, bases, attrs)
                    continue

            else:
                self.add_import(module, class_symbol)

    def add_import(self, module: str, class_str: str):
        """adds an Import to the import dict

        :param module: Python Module of the dict
        :type module: str
        :param class_str: Class that is to be imported
        :type class_str: str
        """
        if self.dimport.get(module):
            self.dimport[module].add(class_str)
        else:
            self.dimport[module] = {class_str}

    def add_mod_class(self, module_cls: str, bases: list[str], attrs: tuple[str, str]):
        """adds module class to the module dict

        :param module_cls: name of the new Module class
        :type module_cls: str
        :param bases: bases the new class is derived from
        :type bases: list[str]
        :param attrs: list of attributes of the class
        :type attrs: tuple[str, str]
        """
        self.dmodule[module_cls] = {"bases": bases, "attrs": attrs}

    def add_node_class(self, node_cls: str, bases: list[str], attrs: tuple[str, str]):
        self.dnode[node_cls] = {"bases": bases, "attrs": attrs}

    def _write_imports_string(self):
        self.import_string = ""

        # Collect imports required for type hints (Node)
        for mod, cls_set in self.dimport.items():
            cls_string = ", ".join(cls_set)

            self.import_string += f"from {mod} import {cls_string} \n"
        self.import_string += "\n\n"

    def _write_mod_cls_string(self):
        self.mod_cls_string = ""

        for mod_cls, cls_dict in self.dmodule.items():
            # Generate the Python code for each Module class
            bases = ", ".join(cls_dict["bases"])

            self.mod_cls_string += f"class {mod_cls}({bases}):\n"
            for attr_name, attr_type in cls_dict["attrs"]:
                self.mod_cls_string += f"    {attr_name}: {attr_type}\n"

            self.mod_cls_string += "\n\n"

    def _write_node_cls_string(self):
        self.node_cls_string = ""

        for node_cls, cls_dict in self.dnode.items():
            # Generate the Python code for each Module class
            bases = ", ".join(cls_dict["bases"])

            self.node_cls_string += f"class {node_cls}({bases}):\n"
            for attr_name, attr_type in cls_dict["attrs"]:
                self.node_cls_string += f"    {attr_name}: {attr_type}\n"

            self.node_cls_string += "\n\n"

    def write_genNodeClass_file(self):
        self._write_imports_string()
        self._write_mod_cls_string()
        self._write_node_cls_string()

        code = ""

        code += self.import_string
        code += self.mod_cls_string
        code += self.node_cls_string

        # Write the generated code to a .py file
        filename = f"{self.ModName}.py"
        with open(filename, "w") as file:
            file.write(code)

            # Reload the Module after its source has been edited
        if self.node_mod is not None:
            reload(self.node_mod)
