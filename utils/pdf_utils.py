# utils/pdf_utils.py
import os
from fpdf import FPDF
from datetime import datetime
import pytz # Para fuso horário
import logging

logger = logging.getLogger(__name__)

class PDFReportPendentes(FPDF):
    def __init__(self, orientation='L', unit='mm', format='A4', gen_info_str="", page_title="Relatório - Pólis", logo_path=None, root_path=None):
        super().__init__(orientation, unit, format)
        self.gen_info_str = gen_info_str
        self.page_title_text = page_title
        self.logo_path = logo_path
        self.root_path = root_path if root_path else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        self.set_left_margin(10)
        self.set_right_margin(10)
        self.set_auto_page_break(auto=True, margin=15) 
        
        self.font_name = 'Arial' 
        self.font_name_bold = 'Arial'
        self.font_name_italic = 'Arial'

        font_dir = os.path.join(self.root_path, 'static', 'fonts')
        regular_font_path = os.path.join(font_dir, 'DejaVuSans.ttf')
        
        try:
            if os.path.exists(regular_font_path):
                self.add_font('DejaVu', '', regular_font_path, uni=True)
                self.add_font('DejaVu', 'B', regular_font_path, uni=True) 
                self.add_font('DejaVu', 'I', regular_font_path, uni=True) 
                self.add_font('DejaVu', 'BI', regular_font_path, uni=True)
                self.font_name = 'DejaVu'
                self.font_name_bold = 'DejaVu'
                self.font_name_italic = 'DejaVu'
                logger.info(f"Fonte Unicode '{self.font_name}' carregada para PDF a partir de {regular_font_path}.")
            else:
                logger.warning(f"Fonte DejaVuSans.ttf não encontrada em {regular_font_path}. Usando Arial.")
        except Exception as e_font:
            logger.error(f"Erro ao carregar fonte PDF: {e_font}. Usando Arial como fallback.")

    def header(self):
        title_x_offset = self.l_margin
        if self.logo_path and os.path.exists(self.logo_path):
            try: 
                logo_w = 15 
                self.image(self.logo_path, x=self.l_margin, y=8, w=logo_w)
                title_x_offset = self.l_margin + logo_w + 5 
            except Exception as e_logo_pdf:
                logger.error(f"Erro ao adicionar logótipo ao PDF ({self.logo_path}): {e_logo_pdf}")
        else:
            if self.logo_path: logger.warning(f"Ficheiro de logótipo para PDF não encontrado: {self.logo_path}")
        
        self.set_font(self.font_name_bold, 'B', 14) 
        available_width = self.w - title_x_offset - self.r_margin
        title_w_text = self.get_string_width(self.page_title_text)
        
        self.set_x(title_x_offset + (available_width - title_w_text) / 2)
        self.cell(title_w_text, 10, self.page_title_text, 0, 1, 'C')
        self.ln(4) 

    def footer(self): 
        self.set_y(-15) 
        self.set_font(self.font_name_italic, 'I', 8) 
        page_num_text = f'Página {self.page_no()}/{{nb}}'
        self.cell(0, 10, page_num_text, 0, 0, 'C')
        self.set_xy(self.l_margin, -15)
        self.set_font(self.font_name_italic, 'I', 8) 
        self.cell(0, 10, self.gen_info_str, 0, 0, 'L')

    def section_title(self, title):
        self.set_font(self.font_name_bold, 'B', 11)
        self.set_fill_color(230,230,230); self.cell(0,7,title,0,1,'L',True); self.ln(3)

    def section_body(self, lines):
        self.set_font(self.font_name,'',9); [self.multi_cell(0,5,str(l),0,'L') for l in lines]; self.ln(2)

    def print_table(self, headers, data, widths):
        self.set_font(self.font_name_bold,'B',7.5); self.set_fill_color(220,220,220); self.set_line_width(0.2); self.set_draw_color(180,180,180)
        for i,h in enumerate(headers): self.cell(widths[i],7,str(h),1,0,'C',True)
        self.ln(); self.set_font(self.font_name,'',7); fill=False
        
        line_height_text = 4 
        cell_padding_y_total = 2 

        for row in data:
            max_lines_in_row = 1
            for i, val in enumerate(row):
                cell_text = str(val if val is not None else 'N/A')
                text_width = widths[i] - (2 * 1) 
                if text_width <= 0: text_width = 1
                self.set_font(self.font_name, '', 7) 
                num_lines = len(self.multi_cell(text_width, line_height_text, cell_text, 0, 'L', split_only=True))
                max_lines_in_row = max(max_lines_in_row, num_lines)
            
            current_row_height = max_lines_in_row * line_height_text + cell_padding_y_total
            current_row_height = max(current_row_height, 7) 

            if self.get_y() + current_row_height > self.page_break_trigger:
                if self.page_no() > 0 or (self.y + current_row_height > self.h - self.b_margin): 
                    self.add_page(self.cur_orientation)
                    self.set_font(self.font_name_bold,'B',7.5); self.set_fill_color(220,220,220)
                    for i,h_col in enumerate(headers): self.cell(widths[i],7,str(h_col),1,0,'C',True)
                    self.ln(); self.set_font(self.font_name,'',7)
            
            self.set_fill_color(*( (245,245,245) if fill else (255,255,255) ))
            
            x_start_line = self.get_x() 
            y_start_line = self.get_y()

            for i,val in enumerate(row):
                x_cell_pos = x_start_line + sum(widths[:i])
                self.rect(x_cell_pos, y_start_line, widths[i], current_row_height, 'DF')
                
                cell_text = str(val if val is not None else 'N/A')
                align = 'R' if headers[i].lower()=="valor" else 'L'
                
                num_lines_this_cell = len(self.multi_cell(widths[i] - 2, line_height_text, cell_text, 0, align, split_only=True))
                text_block_height = num_lines_this_cell * line_height_text
                y_text_pos = y_start_line + (current_row_height - text_block_height) / 2
                y_text_pos = max(y_start_line + (cell_padding_y_total / 2), y_text_pos) 

                self.set_xy(x_cell_pos + 1, y_text_pos) 
                self.multi_cell(widths[i] - 2, line_height_text, cell_text, 0, align, False)
            
            self.set_xy(x_start_line, y_start_line + current_row_height) 
            fill = not fill

def _get_filters_as_text_list(filtros_aplicados_form_dict, format_currency_func):
    """Converte o dicionário de filtros aplicados numa lista de strings para o PDF."""
    lines = []
    if filtros_aplicados_form_dict:
        key_map_display = {
            'pedido_ref': 'Pedido Ref.', 'fornecedor': 'Fornecedor',
            'filial_pend': 'Filial', 'status_pend': 'Status',
            'valor_min': 'Valor Mínimo', 'valor_max': 'Valor Máximo'
        }
        for key_form, value in filtros_aplicados_form_dict.items():
            if value: 
                display_key = key_map_display.get(key_form, key_form.replace("_", " ").title())
                value_display = format_currency_func(value) if 'valor' in key_form else value
                lines.append(f"{display_key}: {value_display}")
    return lines if lines else ["Nenhum filtro aplicado."]


def gerar_pdf_pendencias(pendentes_data, filtros_form, current_username, app_root_path, format_currency_func):
    """
    Gera um PDF para o relatório de pendências.
    :param pendentes_data: Lista de objetos sqlite3.Row com os dados das pendências.
    :param filtros_form: Dicionário com os filtros aplicados.
    :param current_username: Nome do utilizador que gerou o relatório.
    :param app_root_path: Caminho raiz da aplicação Flask.
    :param format_currency_func: Função para formatar valores monetários.
    :return: Bytes do PDF gerado.
    """
    try:
        now_sp = datetime.now(pytz.timezone('America/Sao_Paulo'))
        gen_info = f"Gerado em: {now_sp.strftime('%d/%m/%Y %H:%M:%S')} por {current_username}"
        logo_path = os.path.join(app_root_path, 'static', 'images', 'polis_logo.png')

        pdf = PDFReportPendentes(orientation='L', gen_info_str=gen_info, 
                                 page_title="Relatório de Pendências - Pólis", 
                                 logo_path=logo_path, root_path=app_root_path)
        pdf.alias_nb_pages()
        pdf.add_page()
        
        filter_text_lines = _get_filters_as_text_list(filtros_form, format_currency_func)
        pdf.section_title("Filtros Aplicados")
        pdf.section_body(filter_text_lines)

        headers = ["Pedido Ref.", "Fornecedor", "Filial", "Valor", "Status", "Importado em"]
        widths = [45, 65, 45, 30, 35, 37] 

        data_pdf = []
        if pendentes_data:
            for r in pendentes_data: # 'r' é um objeto sqlite3.Row
                data_pdf.append([
                    r['pedido_ref'] if 'pedido_ref' in r.keys() else 'N/A',
                    r['fornecedor'] if 'fornecedor' in r.keys() else 'N/A',
                    r['filial'] if 'filial' in r.keys() else 'N/A',
                    format_currency_func(r['valor'] if 'valor' in r.keys() else None),
                    r['status'] if 'status' in r.keys() else 'N/A',
                    r['data_importacao_fmt'] if 'data_importacao_fmt' in r.keys() else 'N/A'
                ])
        
        pdf.section_title("Dados das Pendências")
        if data_pdf:
            pdf.print_table(headers, data_pdf, widths)
        else:
            pdf.set_font(pdf.font_name, 'I', 10)
            pdf.cell(0, 10, "Nenhuma pendência encontrada com os filtros aplicados.", 0, 1, 'C')
        
        pdf_output_bytes = pdf.output(dest='S')
        return pdf_output_bytes

    except Exception as e:
        logger.error(f"Erro crítico ao gerar PDF de pendências no utilitário: {e}", exc_info=True)
        raise 
