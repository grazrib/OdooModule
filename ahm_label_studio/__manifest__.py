# -*- coding: utf-8 -*-
{
    "name": "Odoo Product Label Builder PRO | Product Label Builder Pro",
    "summary": "Product Barcode Label Building and Printing | Professional Tool to Print Labels | Barcode Product Label Builder | Product Label Designer | Sticker Label Maker | Dymo Label Maker | Barcode Label Generator | Direct Print | Barcode Labels",
    "version": "18.0.1.3.0",
    "category": "Inventory/Barcode",
    "author": "Ahson Mahmood",
    "website": "https://www.ahsonmahmood.com/",
    "support": "https://www.linkedin.com/in/ahsonmahmood/",
    "license": "OPL-1",
    "price": 34.99,
    "currency": "USD",
    "depends": ["base", "web", "product"],

    "external_dependencies": {},

    "data": [
            "data/paperformat_data.xml",
            "security/ir.model.access.csv",
            "views/label_template_views.xml",
            "wizard/label_print_wizard_views.xml",
            "reports/label_report.xml",
            "views/label_menu.xml",
            "views/product_template_label_views.xml",


            "data/label_data.xml",
            "data/label_data4x7.xml",
            "data/label_data2x7.xml",
            "data/label_data2x7_pic.xml",


    ],
    "assets": {
        'web.assets_backend': [
        ],
    },
    "images": [
        "static/description/main_screenshot.png",

        "static/description/dymo1.png",
        "static/description/a427p1.png",
        "static/description/a447.png",
        "static/description/a427.png",

        "static/description/app.png",
        "static/description/screebshotm.png",
        "static/description/screenshotm.png",
        "static/description/screenshot3.png",
        "static/description/screenshot4.png",
        "static/description/screenshot5.png",
    ],
    "application": True,
    "support": "contact@ahsonmahmood.com",
    "demo": [],
    "installable": True,
}
