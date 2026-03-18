"""Annotator for the ansible.builtin.dnf module."""

from apme_engine.engine.annotators.module_annotator_base import ModuleAnnotator, ModuleAnnotatorResult
from apme_engine.engine.models import DefaultRiskType, PackageInstallDetail, RiskAnnotation, TaskCall


class DnfAnnotator(ModuleAnnotator):
    """Annotates dnf tasks with package install risk details.

    Attributes:
        fqcn: Fully qualified module name.
        enabled: Whether this annotator is active.

    """

    fqcn: str = "ansible.builtin.dnf"
    enabled: bool = True

    def run(self, task: TaskCall) -> ModuleAnnotatorResult:
        """Extract package install risk from dnf task arguments.

        Args:
            task: The task call to analyze.

        Returns:
            Result with package install risk annotations.

        """
        pkg = task.args.get("name")
        allow_downgrade = task.args.get("allow_downgrade")
        validate_certs = task.args.get("validate_certs")
        disable_gpg_check = task.args.get("disable_gpg_check")

        annotation = RiskAnnotation.init(
            risk_type=DefaultRiskType.PACKAGE_INSTALL,
            detail=PackageInstallDetail(
                _pkg_arg=pkg,
                _validate_certs_arg=validate_certs,
                _allow_downgrade_arg=allow_downgrade,
                _disable_gpg_check_arg=disable_gpg_check,
            ),
        )
        return ModuleAnnotatorResult(annotations=[annotation])
