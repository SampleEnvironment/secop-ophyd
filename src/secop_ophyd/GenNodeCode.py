import inspect
import re
from importlib import import_module, reload
from inspect import Signature
from pathlib import Path


class Method:
    def __init__(self, cmd_name: str, description: str, cmd_sign: Signature) -> None:
        self.sig_str: str

        raw_sig_str: str = str(cmd_sign)

        raw_sig_str = raw_sig_str.replace("typing.", "")

        if "self" in raw_sig_str:
            self.sig_str = raw_sig_str
        else:
            self.sig_str = "(self, " + raw_sig_str[1:]
        self.name: str = cmd_name
        self.description: str = description

    def __str__(self) -> str:
        code = ""
        code += "    @abstractmethod \n"
        code += f"    def {self.name}{self.sig_str}:\n"
        code += f'      """{self.description}"""'

        return code


class GenNodeCode:
    """Generates A Python Class for a given SECoP_Node_Device instance. This allows
    autocompletiion and type hinting in IDEs, this is needed since the attributes of
    the generated Ophyd devices are only known at runtime.
    """

    ModName: str = "genNodeClass"
    node_mod = None
    module_folder_path: Path | None = None

    def __init__(self, path: str | None = None, log=None):
        """Instantiates GenNodeCode, internally all atrribues on a node and module level
        are collected. Additionally all the needed imports are collected in a dict
        """
        # prevent circular import
        from secop_ophyd.SECoPDevices import (
            SECoPBaseDevice,
            SECoPCommunicatorDevice,
            SECoPMoveableDevice,
            SECoPNodeDevice,
            SECoPReadableDevice,
            SECoPTriggerableDevice,
            SECoPWritableDevice,
        )

        self.dimport: dict[str, set[str]] = {}
        self.dmodule: dict = {}
        self.dnode: dict = {}

        self.import_string: str = ""
        self.mod_cls_string: str = ""
        self.node_cls_string: str = ""

        self.log = log

        if path is not None:
            self.module_folder_path = Path(path)

        # resulting Class is supposed to be abstract
        self.add_import("abc", "ABC")
        self.add_import("abc", "abstractmethod")
        self.add_import("typing", "Any")

        mod_path = self.ModName

        if self.module_folder_path is not None:
            str_path = str(self.module_folder_path)
            rep_slash = str_path.replace("/", ".").replace("\\", ".")
            mod_path = f"{rep_slash}.{self.ModName}"

        try:
            self.node_mod = import_module(mod_path)
        except ModuleNotFoundError:
            if self.log is None:
                print("no code generated yet, building from scratch")
            else:
                self.log.info("no code generated yet, building from scratch")
            return

        modules = inspect.getmembers(self.node_mod)

        results = filter(lambda m: inspect.isclass(m[1]), modules)
        for class_symbol, class_obj in results:
            module = class_obj.__module__

            if module == self.ModName:
                # Node Classes

                def get_attrs(source: str) -> list[tuple[str, str]]:
                    source_list: list[str] = source.split("\n")
                    # remove first line
                    source_list.pop(0)
                    source_list.pop()

                    # remove whitespace
                    source_list = [attr.replace(" ", "") for attr in source_list]
                    # split at colon
                    attrs: list[tuple[str, str]] = []
                    for attr in source_list:
                        parts = attr.split(":", maxsplit=1)
                        if len(parts) == 2:
                            attrs.append((parts[0], parts[1]))

                    return attrs

                if issubclass(class_obj, SECoPNodeDevice):
                    attrs = get_attrs(inspect.getsource(class_obj))

                    bases = [base.__name__ for base in class_obj.__bases__]

                    self.add_node_class(class_symbol, bases, attrs)
                    continue

                if issubclass(
                    class_obj,
                    (
                        SECoPBaseDevice,
                        SECoPCommunicatorDevice,
                        SECoPMoveableDevice,
                        SECoPReadableDevice,
                        SECoPWritableDevice,
                        SECoPTriggerableDevice,
                    ),
                ):

                    attributes = []

                    for attr_name, attr in class_obj.__annotations__.items():
                        attributes.append((attr_name, attr.__name__))

                    methods = []

                    for method_name, method in class_obj.__dict__.items():
                        if callable(method) and not method_name.startswith("__"):

                            method_source = inspect.getsource(method)

                            match = re.search(
                                r"\s*def\s+\w+\s*\(.*\).*:\s*", method_source
                            )
                            if match:
                                function_body = method_source[match.end() :]
                                description_list = function_body.split('"""', 2)
                                description = description_list[1]
                            else:
                                raise Exception(
                                    "could not extract description function body"
                                )

                            methods.append(
                                Method(
                                    method_name, description, inspect.signature(method)
                                )
                            )

                    bases = [base.__name__ for base in class_obj.__bases__]

                    self.add_mod_class(class_symbol, bases, attributes, methods)
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

    def add_mod_class(
        self,
        module_cls: str,
        bases: list[str],
        attrs: list[tuple[str, str]],
        cmd_plans: list[Method],
    ):
        """adds module class to the module dict

        :param module_cls: name of the new Module class
        :type module_cls: str
        :param bases: bases the new class is derived from
        :type bases: list[str]
        :param attrs: list of attributes of the class
        :type attrs: tuple[str, str]
        """
        self.dmodule[module_cls] = {"bases": bases, "attrs": attrs, "plans": cmd_plans}

    def add_node_class(
        self, node_cls: str, bases: list[str], attrs: list[tuple[str, str]]
    ):
        self.dnode[node_cls] = {"bases": bases, "attrs": attrs}

    def _write_imports_string(self):
        self.import_string = ""

        # Collect imports required for type hints (Node)
        for mod, cls_set in self.dimport.items():
            if len(cls_set) == 1 and list(cls_set)[0] == "":
                self.import_string += f"import {mod} \n"
                continue

            cls_string = ", ".join(cls_set)

            self.import_string += f"from {mod} import {cls_string} \n"
        self.import_string += "\n\n"

    def _write_mod_cls_string(self):
        self.mod_cls_string = ""

        for mod_cls, cls_dict in self.dmodule.items():
            # Generate the Python code for each Module class
            bases = ", ".join(cls_dict["bases"])

            self.mod_cls_string += f"class {mod_cls}({bases}):\n"
            # write Attributes
            for attr_name, attr_type in cls_dict["attrs"]:
                self.mod_cls_string += f"    {attr_name}: {attr_type}\n"
            self.mod_cls_string += "\n"
            # write abstract methods
            for plan in cls_dict["plans"]:
                self.mod_cls_string += str(plan)

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

    def write_gen_node_class_file(self):
        self._write_imports_string()
        self._write_mod_cls_string()
        self._write_node_cls_string()

        code = ""

        code += self.import_string
        code += self.mod_cls_string
        code += self.node_cls_string

        # Write the generated code to a .py file

        if self.module_folder_path is None:
            filep = Path(f"{self.ModName}.py")
        else:
            filep = self.module_folder_path / f"{self.ModName}.py"
        with open(filep, "w") as file:
            file.write(code)

        # Reload the Module after its source has been edited
        if self.node_mod is not None:
            reload(self.node_mod)
