"""FastAPI dependencies: the services the app was built with."""

from typing import Annotated

from fastapi import Depends, Request

from book_agent.api.services import ApiServices


def get_services(request: Request) -> ApiServices:
    services: ApiServices = request.app.state.services
    return services


Services = Annotated[ApiServices, Depends(get_services)]
