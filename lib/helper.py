import os
import xml.etree.cElementTree as etree
import re
import ctypes as ct

SHLoadIndirectString = ct.windll.shlwapi.SHLoadIndirectString
SHLoadIndirectString.argtypes = [ct.c_wchar_p, ct.c_wchar_p, ct.c_uint, ct.POINTER(ct.c_void_p)]
SHLoadIndirectString.restype = ct.HRESULT

RESOURCE_PREFIX = "ms-resource:"

class AppXPackage(object):
    """Represents a windows app package
    """

    def __init__(self, property_dict):
        """Sets needed properties from the dict as member
        """
        # for key, value in property_dict.items():
        #     setattr(self, key, value)

        self.Name = property_dict["Name"] if "Name" in property_dict else None
        self.InstallLocation = property_dict["InstallLocation"] if "InstallLocation" in property_dict else None
        self.PackageFamilyName = property_dict["PackageFamilyName"] if "PackageFamilyName" in property_dict else None
        self.applications = []

    async def apps(self):
        if not self.applications:
            self.applications = await self._get_applications()
        return self.applications

    async def _get_applications(self):
        """Reads the manifest of the package and extracts name, description, applications and logos
        """
        manifest_path = os.path.join(self.InstallLocation, "AppxManifest.xml")
        if not os.path.isfile(manifest_path):
            return []
        manifest = etree.parse(manifest_path)
        ns = {"default": re.sub(r"\{(.*?)\}.+", r"\1", manifest.getroot().tag)}

        package_applications = manifest.findall("./default:Applications/default:Application", ns)
        if not package_applications:
            return []

        apps = []

        package_identity = ""
        package_identity_node = manifest.find("./default:Identity", ns)
        if package_identity_node is not None:
            package_identity = package_identity_node.get("Name").strip()

        package_description = ""
        default_description_node = manifest.find("./default:Properties/default:Description", ns)
        if default_description_node is not None:
            package_description = default_description_node.text.strip()

        package_display_name = ""
        default_display_name_node = manifest.find("./default:Properties/default:DisplayName", ns)
        if default_display_name_node is not None:
            package_display_name = default_display_name_node.text.strip()

        package_icon_path = ""
        logo_node = manifest.find("./default:Properties/default:Logo", ns)
        if logo_node is not None:
            logo = logo_node.text
            package_icon_path = os.path.join(self.InstallLocation, logo)

        for application in package_applications:
            app_display_name = ""
            app_description = ""
            app_icon_paths = {}
            app_misc = False

            visual_elements = application.find("./*[@DisplayName]", ns)
            if visual_elements:
                default_tile = visual_elements.find("./*[@Square310x310Logo]", ns)
                if not default_tile:
                    default_tile = visual_elements.find("./*[@ShortName]", ns)
                
                app_misc = visual_elements.get("AppListEntry") == "none" \
                    if "AppListEntry" in visual_elements.attrib else False
                

                app_display_name = visual_elements.get("DisplayName").strip()
                app_description = visual_elements.get("Description").strip()

                logos = [attr for attr in visual_elements.attrib if "logo" in attr.lower()]
                if default_tile:
                    logos.extend([attr for attr in default_tile.attrib if "logo" in attr.lower()])
                square_logos = [logo for logo in logos if "square" in logo.lower()]
                wide_logos = [logo for logo in logos if "wide" in logo.lower()]
                if square_logos:
                    biggest = max(square_logos, key=lambda x: int(re.search(r"(\d+)x\d+", x).groups()[0]))
                    app_icon_paths["square"] = os.path.join(self.InstallLocation, visual_elements.get(biggest) if biggest in visual_elements.attrib else default_tile.get(biggest))
                if wide_logos:
                    biggest = max(wide_logos, key=lambda x: re.search(r"(\d+)x\d+", x).groups()[0])
                    app_icon_paths["wide"] = os.path.join(self.InstallLocation, visual_elements.get(biggest) if biggest in visual_elements.attrib else default_tile.get(biggest))
                if logos:
                    biggest = min(logos)
                    app_icon_paths["logo"] = os.path.join(self.InstallLocation, visual_elements.get(biggest) if biggest in visual_elements.attrib else default_tile.get(biggest))
                
                app_icon_paths["package"] = package_icon_path

                if app_display_name.startswith(RESOURCE_PREFIX):
                    resource = self._get_resource(self.InstallLocation, package_identity, app_display_name)
                    if resource:
                        app_display_name = resource

                if app_description.startswith(RESOURCE_PREFIX):
                    resource = self._get_resource(self.InstallLocation, package_identity, app_description)
                    if resource:
                        app_description = resource

            if not app_display_name or app_display_name.startswith(RESOURCE_PREFIX):
                if package_display_name.startswith(RESOURCE_PREFIX):
                    resource = self._get_resource(self.InstallLocation, package_identity, package_display_name)
                    if resource:
                        package_display_name = resource
                    else:
                        continue
                if not package_display_name.startswith(RESOURCE_PREFIX):
                    app_display_name = package_display_name

            if not app_description or app_description.startswith(RESOURCE_PREFIX):
                if package_description.startswith(RESOURCE_PREFIX):
                    resource = self._get_resource(self.InstallLocation, package_identity, package_description)
                    if resource:
                        package_description = resource

                if not package_description.startswith(RESOURCE_PREFIX):
                    app_description = package_description

            apps.append(AppX(execution="shell:AppsFolder\\{}!{}".format(self.PackageFamilyName, application.get("Id")),
                            display_name=app_display_name,
                            description=app_description,
                            icon_paths=app_icon_paths,
                            app_id="{}!{}".format(self.PackageFamilyName, application.get("Id")),
                            misc_app=app_misc))
        return apps

    @staticmethod
    def _get_resource(install_location, package_id, resource):
        """Helper method to resolve resource strings to their (localized) value
        """
        # this has resolved every resource I could find with 1 API call.
        try:
            if resource[0:12] == RESOURCE_PREFIX:
                resource_key = resource[12:]
                if resource_key.startswith("//"):
                    resource_path = resource
                elif resource_key.startswith("/"):
                    resource_path = RESOURCE_PREFIX + "//" + resource_key
                elif resource_key.find("/") != -1:
                    resource_path = RESOURCE_PREFIX + "/" + resource_key
                else:
                    resource_path = RESOURCE_PREFIX + "///resources/" + resource_key

                resource_descriptor = "@{{{}\\resources.pri? {}}}".format(install_location,resource_path)

                inp = ct.create_unicode_buffer(resource_descriptor)
                output = ct.create_unicode_buffer(1024)
                result = SHLoadIndirectString(inp, output, ct.sizeof(output), None)
                if result == 0 and output.value:
                    if not output.value.startswith(RESOURCE_PREFIX):
                        return output.value
        except OSError:
            pass

        return None


class AppX(object):
    """Represents an executable application from a windows app package
    """
    def __init__(self, execution=None, display_name=None, description=None, icon_paths=None, app_id=None, misc_app=False):
        self.execution = execution
        self.display_name = display_name
        self.description = description
        self.icon_paths = icon_paths
        self.app_id = app_id
        self.misc_app = misc_app
