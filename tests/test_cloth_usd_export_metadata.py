"""Test the read-only USD signature check without importing the MD runtime."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from md_mcp_plugin.embedded import cloth_usd_export_metadata as metadata


class UnexpectedAccess:
    def __get__(self, *args):
        raise AssertionError("A metadata check must not read a descriptor value")


class UsdExportMetadataTests(unittest.TestCase):
    def _runtime(self, doc, *, omit_option=None):
        def export(*args, **kwargs):
            raise AssertionError("A metadata check must not export")

        def construct(*args, **kwargs):
            raise AssertionError("A metadata check must not construct options")

        export.__doc__ = doc
        options = {
            name: type(name, (), {
                "__init__": construct,
                **{field: UnexpectedAccess() for field in fields if field != omit_option},
            })
            for name, fields in metadata._OPTIONS.items()
        }
        return {"export_api": SimpleNamespace(ExportUSD=export), "ApiTypes": SimpleNamespace(**options)}

    def test_observes_exact_documented_path_overload_without_calls(self):
        runtime = self._runtime(
            "ExportUSD(*args, **kwargs)\nOverloaded function.\n"
            "1. ExportUSD(arg0: ApiTypes.ImportExportOption, arg1: ApiTypes.ExportUSDOption) -> List[str]\n"
            "2. ExportUSD(arg0: str, arg1: ApiTypes.ImportExportOption, arg2: ApiTypes.ExportUSDOption) -> List[str]\n"
        )
        with patch.object(metadata, "import_module", side_effect=runtime.__getitem__):
            result = metadata.probe()
        self.assertTrue(result["documented_export_binding_observed"])
        self.assertEqual(len(result["exporters"]["ExportUSD"]["documented_explicit_path_overloads"]), 1)
        self.assertEqual(result["option_instances_constructed"], 0)
        self.assertEqual(result["export_calls"], 0)
        self.assertFalse(result["export_dispatch_authorized"])
        self.assertFalse(result["solver_reference_xyz_verified"])

    def test_rejects_unknown_argument_shape_result_or_missing_option(self):
        good = "ExportUSD(_filePath: str, _options: ImportExportOption, _usdOptions: ExportUSDOption) -> list[str]"
        for doc, missing in (
            (good.replace("list[str]", "None"), None),
            (good.replace("_filePath: str, ", ""), None),
            (good.replace("ExportUSD(", "ExportUSDW("), None),
            (good, "m_bExportSimulationData"),
            (good + "\n" + good, None),
        ):
            with self.subTest(doc=doc, missing=missing), patch.object(
                metadata, "import_module", side_effect=self._runtime(doc, omit_option=missing).__getitem__
            ):
                self.assertFalse(metadata.probe()["documented_export_binding_observed"])

    def test_no_installed_binding_is_an_unobserved_result(self):
        with patch.object(metadata, "import_module", side_effect=ImportError):
            result = metadata.probe()
        self.assertFalse(result["documented_export_binding_observed"])
        self.assertEqual(result["modules_present"], {"export_api": False, "ApiTypes": False})
        self.assertEqual(result["export_calls"], 0)


if __name__ == "__main__":
    unittest.main()
