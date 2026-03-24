from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from api_service.ui_boot import generate_boot_payload
from api_service.ui_assets import ui_assets

router = APIRouter()

@router.get("/test-tasks-home", response_class=HTMLResponse)
def test_tasks_home():
    boot_payload = generate_boot_payload("tasks-home")
    assets_html = ui_assets("tasks-home")

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Tasks Home Test</title>
        {assets_html}
    </head>
    <body class="bg-gray-50 text-gray-900 p-8">
        <div id="mission-control-root"></div>
        <script id="moonmind-ui-boot" type="application/json">
            {boot_payload}
        </script>
    </body>
    </html>
    """
    return html
