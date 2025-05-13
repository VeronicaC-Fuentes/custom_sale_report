from odoo import models, fields, api

class SaleCostReport(models.Model):
    _name = 'custom.sale.report'
    _description = 'Vista de Reporte de Ventas y Costos Mejorada'
    _auto = False  

    compania = fields.Char(string="Compañía")
    fecha_factura = fields.Date(string="Fecha Factura")
    mes_factura = fields.Char(string="Mes de Factura")
    tipo_documento = fields.Char(string="Tipo de Documento")
    numero = fields.Char(string="Número")
    socio = fields.Char(string="Socio")
    vendedor = fields.Char(string="Vendedor")
    terminos_pago = fields.Char(string="Términos de Pago")
    producto_nombre = fields.Char(string="Producto")
    cantidad = fields.Float(string="Cantidad")
    producto_costo = fields.Float(string="Costo Unitario")
    precio_unitario = fields.Float(string="Precio Unitario")
    subtotal_soles = fields.Float(string="Subtotal en Soles")
    costo_total = fields.Float(string="Costo Total")
    margen = fields.Float(string="Margen")
    contribucion = fields.Float(string="Contribución")
    # Eliminamos tot_soles por ser igual al subtotal
    # tot_soles = fields.Float(string="Total en Soles")
    sector = fields.Char(string="Sector")
    ciudad = fields.Char(string="Ciudad")

    @api.model
    def init(self):
        self.env.cr.execute("""
            DROP VIEW IF EXISTS custom_sale_report CASCADE;
            CREATE OR REPLACE VIEW custom_sale_report AS (
                WITH valid_lines AS (
                    SELECT
                        aml.id,
                        am.id AS move_id,
                        aml.product_id,
                        am.name AS numero,
                        COALESCE(am.invoice_date, am.create_date::date) AS fecha_factura,
                        TO_CHAR(COALESCE(am.invoice_date, am.create_date::date), 'YYYY-MM') AS mes_factura,
                        CASE
                            WHEN am.move_type = 'out_refund' AND LEFT(am.name, 1) = 'F' THEN 'Nota de Crédito - Factura'
                            WHEN am.move_type = 'out_refund' AND LEFT(am.name, 1) = 'B' THEN 'Nota de Crédito - Boleta'
                            WHEN am.move_type = 'out_invoice' AND LEFT(am.name, 1) = 'F' THEN 'Factura'
                            WHEN am.move_type = 'out_invoice' AND LEFT(am.name, 1) = 'B' THEN 'Boleta'
                            ELSE 'Otros'
                        END AS tipo_documento,
                        CASE 
                            WHEN LOWER(rp.name) = 'facturacion' THEN rp.commercial_company_name
                            ELSE rp.name
                        END AS socio,
                        rp_vendedor.name AS vendedor,
                        apt.name->>'es_PE' AS terminos_pago,
                        pt.name->>'es_PE' AS producto_nombre,
                        rpi.name->>'es_PE' AS sector,
                        rp.city AS ciudad,
                        
                        -- (1) Cantidad: negativa en notas de crédito, positiva en factura
                        CASE 
                            WHEN am.move_type = 'out_refund' THEN -aml.quantity
                            ELSE aml.quantity
                        END AS cantidad,

                        -- (2) Precio unitario (en la moneda de la factura) en valor absoluto
                        ABS(aml.price_unit) AS price_unit,

                        -- (3) Subtotal en soles (por línea) => ya viene en moneda de la compañía
                        -aml.balance AS subtotal_soles,

                        am.company_id,

                        -- Para asegurar que seleccionamos la línea visible en la factura
                        ROW_NUMBER() OVER(
                            PARTITION BY am.id, aml.product_id
                            ORDER BY aml.sequence, aml.id
                        ) AS rn

                    FROM account_move_line aml
                    JOIN account_move am ON aml.move_id = am.id
                    JOIN res_partner rp ON am.partner_id = rp.id
                    LEFT JOIN res_users ru_vendedor ON am.invoice_user_id = ru_vendedor.id
                    LEFT JOIN res_partner rp_vendedor ON ru_vendedor.partner_id = rp_vendedor.id
                    LEFT JOIN account_payment_term apt ON am.invoice_payment_term_id = apt.id
                    LEFT JOIN product_product pp ON aml.product_id = pp.id
                    LEFT JOIN product_template pt ON pp.product_tmpl_id = pt.id
                    LEFT JOIN res_partner_industry rpi ON rp.industry_id = rpi.id
                    WHERE am.state = 'posted'
                      AND am.move_type IN ('out_invoice', 'out_refund')
                      AND (aml.quantity != 0 OR aml.price_unit > 0)
                      AND aml.display_type = 'product'
                )
                SELECT
                    ROW_NUMBER() OVER () AS id,
                    vl.fecha_factura,
                    vl.mes_factura,
                    vl.tipo_documento,
                    vl.numero,
                    vl.socio,
                    vl.vendedor,
                    vl.terminos_pago,
                    vl.producto_nombre,
                    vl.sector,
                    vl.ciudad,
                    vl.cantidad,
                    vl.subtotal_soles,
                    
                    -- (4) Precio unitario: calculado dividiendo el subtotal en soles entre la cantidad
                    CASE 
                        WHEN vl.cantidad <> 0 THEN ABS(vl.subtotal_soles / vl.cantidad)
                        ELSE 0
                    END AS precio_unitario,

                    rc.name AS compania,

                    -- (6) Producto costo: se toma la última capa (último movimiento) para el producto
                    (
                        SELECT svl.unit_cost
                        FROM stock_valuation_layer svl
                        WHERE svl.product_id = vl.product_id
                          AND svl.company_id = vl.company_id
                          AND svl.unit_cost > 0
                        ORDER BY svl.create_date DESC, svl.id DESC
                        LIMIT 1
                    ) AS producto_costo,

                    -- (7) Costo total (en soles) = cantidad * unit_cost
                    vl.cantidad * (
                        SELECT svl.unit_cost
                        FROM stock_valuation_layer svl
                        WHERE svl.product_id = vl.product_id
                          AND svl.company_id = vl.company_id
                          AND svl.unit_cost > 0
                        ORDER BY svl.create_date DESC, svl.id DESC
                        LIMIT 1
                    ) AS costo_total,

                    -- (8) Margen = precio de venta (calculado) - costo unitario (última capa)
                    CASE
                        WHEN vl.cantidad <> 0 THEN
                            (ABS(vl.subtotal_soles / vl.cantidad)) -
                            (
                                SELECT svl.unit_cost
                                FROM stock_valuation_layer svl
                                WHERE svl.product_id = vl.product_id
                                  AND svl.company_id = vl.company_id
                                  AND svl.unit_cost > 0
                                ORDER BY svl.create_date DESC, svl.id DESC
                                LIMIT 1
                            )
                        ELSE 0
                    END AS margen,

                    -- (9) Contribución = subtotal_soles - costo_total (calculada por línea)
                    vl.subtotal_soles - (
                        vl.cantidad * (
                            SELECT svl.unit_cost
                            FROM stock_valuation_layer svl
                            WHERE svl.product_id = vl.product_id
                              AND svl.company_id = vl.company_id
                              AND svl.unit_cost > 0
                            ORDER BY svl.create_date DESC, svl.id DESC
                            LIMIT 1
                        )
                    ) AS contribucion

                FROM valid_lines vl
                JOIN res_company rc ON vl.company_id = rc.id
                JOIN account_move am ON am.id = vl.move_id
                WHERE vl.rn = 1
            )
        """)
