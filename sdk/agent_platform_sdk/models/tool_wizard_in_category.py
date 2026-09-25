from enum import Enum


class ToolWizardInCategory(str, Enum):
    DOMAIN_CAPABILITY = "domain_capability"
    IMAGE_GENERATION = "image_generation"
    PLATFORM_CAPABILITY = "platform_capability"
    SERVICE_CONNECTOR = "service_connector"

    def __str__(self) -> str:
        return str(self.value)
