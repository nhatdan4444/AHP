
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
import numpy as np
from pymongo import MongoClient
from bson.objectid import ObjectId
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
from datetime import datetime
import pandas as pd
from io import BytesIO
import logging
from itertools import zip_longest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Cấu hình logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.secret_key = "ahp_secret_key"
app.jinja_env.globals.update(enumerate=enumerate)

# Đăng ký font Times New Roman
try:
    pdfmetrics.registerFont(TTFont('TimesNewRoman', 'times.ttf'))
    logging.info("Đăng ký font Times New Roman thành công")
except Exception as e:
    logging.error(f"Lỗi đăng ký font Times New Roman: {str(e)}")
    raise Exception("Không thể đăng ký font Times New Roman. Vui lòng đảm bảo file times.ttf tồn tại.")

# Kết nối MongoDB
try:
    client = MongoClient('mongodb://localhost:27017/')
    db = client['ahp_investment_db']
    criteria_collection = db['criteria']
    alternatives_collection = db['alternatives']
    comparisons_collection = db['pairwise_comparisons']
    results_collection = db['results']
    logging.info("Kết nối MongoDB thành công")
except Exception as e:
    logging.error(f"Lỗi kết nối MongoDB: {str(e)}")
    raise Exception("Không thể kết nối đến MongoDB. Vui lòng kiểm tra dịch vụ MongoDB.")

# Thư mục lưu biểu đồ
CHART_DIR = os.path.join('static', 'charts')
if not os.path.exists(CHART_DIR):
    os.makedirs(CHART_DIR)
    logging.info(f"Tạo thư mục {CHART_DIR}")

# Hàm tính toán AHP
def calculate_weights(matrix):
    col_sums = matrix.sum(axis=0)
    normalized_matrix = matrix / col_sums
    return normalized_matrix.mean(axis=1)

def check_consistency(matrix, weights):
    n = matrix.shape[0]
    if len(weights) != n:
        raise ValueError(f"Kích thước trọng số ({len(weights)}) không khớp với ma trận ({n}x{n})")
    lambda_max = sum((matrix @ weights) / weights) / n
    CI = (lambda_max - n) / (n - 1)
    RI = [0, 0, 0.58, 0.9, 1.12, 1.24, 1.32, 1.41, 1.45][n-1]
    CR = CI / RI if RI > 0 else 0
    return lambda_max, CI, CR

# Hàm tạo biểu đồ số lượng tiêu chí và phương án
def create_data_summary_chart(criteria_count, alternatives_count):
    try:
        plt.figure(figsize=(6, 4))
        labels = ['Tiêu chí', 'Phương án']
        counts = [criteria_count, alternatives_count]
        plt.bar(labels, counts, color=['skyblue', 'lightgreen'])
        plt.title('Tổng quan dữ liệu')
        plt.ylabel('Số lượng')
        chart_filename = f"data_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        chart_path = os.path.join(CHART_DIR, chart_filename)
        plt.savefig(chart_path, bbox_inches='tight')
        plt.close()
        logging.info(f"Tạo biểu đồ tổng quan: {chart_path}")
        return os.path.join('charts', chart_filename).replace('\\', '/')
    except Exception as e:
        logging.error(f"Lỗi tạo biểu đồ tổng quan: {str(e)}")
        raise

# Hàm tạo biểu đồ tròn cho trọng số tiêu chí
def create_criteria_weights_chart(labels, weights):
    try:
        plt.figure(figsize=(6, 6))
        plt.pie(weights, labels=labels, autopct='%1.1f%%', startangle=140, colors=['#ff9999','#66b3ff','#99ff99','#ffcc99'])
        plt.title('Trọng số tiêu chí')
        chart_filename = f"criteria_weights_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        chart_path = os.path.join(CHART_DIR, chart_filename)
        plt.savefig(chart_path, bbox_inches='tight')
        plt.close()
        logging.info(f"Tạo biểu đồ trọng số tiêu chí: {chart_path}")
        return os.path.join('charts', chart_filename).replace('\\', '/')
    except Exception as e:
        logging.error(f"Lỗi tạo biểu đồ trọng số tiêu chí: {str(e)}")
        raise

# Hàm tạo biểu đồ cột cho trọng số phương án theo từng tiêu chí
def create_alternatives_weights_chart(criteria_name, labels, weights):
    try:
        plt.figure(figsize=(8, 5))
        plt.bar(labels, weights, color='lightcoral')
        plt.title(f'Trọng số phương án theo tiêu chí: {criteria_name}')
        plt.xlabel('Phương án')
        plt.ylabel('Trọng số')
        plt.xticks(rotation=45, ha='right')
        chart_filename = f"alternatives_weights_{criteria_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        chart_path = os.path.join(CHART_DIR, chart_filename)
        plt.savefig(chart_path, bbox_inches='tight')
        plt.close()
        logging.info(f"Tạo biểu đồ trọng số phương án: {chart_path}")
        return os.path.join('charts', chart_filename).replace('\\', '/')
    except Exception as e:
        logging.error(f"Lỗi tạo biểu đồ trọng số phương án: {str(e)}")
        raise

# Hàm nhập dữ liệu từ Excel
@app.route('/import_excel', methods=['POST'])
def import_excel():
    if 'excel_file' not in request.files:
        flash("Vui lòng chọn file Excel để nhập!")
        return redirect(url_for('index'))
    
    file = request.files['excel_file']
    if file.filename == '':
        flash("Vui lòng chọn file Excel để nhập!")
        return redirect(url_for('index'))
    
    if not file.filename.endswith('.xlsx'):
        flash("File phải có định dạng .xlsx!")
        return redirect(url_for('index'))
    
    try:
        xl = pd.ExcelFile(file)
        criteria_collection.delete_many({})
        alternatives_collection.delete_many({})
        
        file_name = file.filename.lower()
        if "ahp nâng cao điểm số" in file_name:
            sheet = xl.parse(xl.sheet_names[0])
            criteria = []
            for col in sheet.columns[1:6]:
                if pd.notna(col) and isinstance(col, str) and col.strip():
                    criteria.append(col.strip())
                    criteria_collection.insert_one({"name": col.strip()})
            
            if len(criteria) < 2:
                flash("Cần ít nhất 2 tiêu chí hợp lệ trong tab đầu tiên!")
                return redirect(url_for('index'))
            
            alternatives = []
            alt_start_row = sheet[sheet.iloc[:, 0] == 'Phương án'].index[0] + 1
            for i in range(alt_start_row, alt_start_row + 4):
                alt_name = sheet.iloc[i, 0]
                if pd.notna(alt_name) and isinstance(alt_name, str) and alt_name.strip():
                    alternatives.append(alt_name.strip())
                    alternatives_collection.insert_one({"name": alt_name.strip()})
            
            if len(alternatives) < 2:
                flash("Cần ít nhất 2 phương án hợp lệ!")
                return redirect(url_for('index'))
        
        else:
            sheet = xl.parse(xl.sheet_names[0])
            criteria = []
            for idx in range(4):
                name = sheet.iloc[idx, 0]
                if pd.notna(name) and isinstance(name, str) and name.strip() and "Weighted Sum" not in name and "λ_max" not in name and "CI" not in name and "CR" not in name:
                    criteria.append(name.strip())
                    criteria_collection.insert_one({"name": name.strip()})
            
            if len(criteria) < 2:
                flash("Cần ít nhất 2 tiêu chí hợp lệ trong tab đầu tiên!")
                return redirect(url_for('index'))
            
            alternatives = set()
            for tab in xl.sheet_names[1:-1]:
                sheet = xl.parse(tab)
                for col in sheet.columns[1:5]:
                    if pd.notna(col) and isinstance(col, str) and col.strip() and "Tổng cột" not in col and "Trọng số" not in col and "λ_max" not in col and "CI" not in col and "CR" not in col:
                        alternatives.add(col.strip())
            
            alternatives = list(alternatives)
            if len(alternatives) < 2:
                flash("Cần ít nhất 2 phương án hợp lệ!")
                return redirect(url_for('index'))
            
            for alt in alternatives:
                alternatives_collection.insert_one({"name": alt})
        
        chart_path = create_data_summary_chart(len(criteria), len(alternatives))
        db['import_logs'].insert_one({
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "criteria_count": len(criteria),
            "alternatives_count": len(alternatives),
            "chart": chart_path,
            "errors": []
        })
        logging.info(f"Nhập Excel thành công: {len(criteria)} tiêu chí, {len(alternatives)} phương án")
        flash("Nhập tiêu chí và phương án từ Excel thành công!")
    except Exception as e:
        logging.error(f"Lỗi nhập Excel: {str(e)}")
        flash(f"Lỗi khi nhập dữ liệu: {str(e)}")
    
    return redirect(url_for('index'))

# Trang chính
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'add_criteria' in request.form:
            criteria = request.form['criteria'].strip()
            if not criteria:
                flash("Tên tiêu chí không được để trống!")
            elif criteria_collection.find_one({"name": criteria}):
                flash("Tiêu chí đã tồn tại!")
            else:
                try:
                    criteria_collection.insert_one({"name": criteria})
                    criteria_count = criteria_collection.count_documents({})
                    alternatives_count = alternatives_collection.count_documents({})
                    chart_path = create_data_summary_chart(criteria_count, alternatives_count)
                    db['import_logs'].insert_one({
                        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "criteria_count": criteria_count,
                        "alternatives_count": alternatives_count,
                        "chart": chart_path,
                        "errors": []
                    })
                    logging.info(f"Thêm tiêu chí: {criteria}")
                    flash("Tiêu chí đã được thêm!")
                except Exception as e:
                    logging.error(f"Lỗi thêm tiêu chí: {str(e)}")
                    flash(f"Lỗi khi thêm tiêu chí: {str(e)}")
        
        elif 'add_alternative' in request.form:
            alternative = request.form['alternative'].strip()
            if not alternative:
                flash("Tên phương án không được để trống!")
            elif alternatives_collection.find_one({"name": alternative}):
                flash("Phương án đã tồn tại!")
            else:
                try:
                    alternatives_collection.insert_one({"name": alternative})
                    criteria_count = criteria_collection.count_documents({})
                    alternatives_count = alternatives_collection.count_documents({})
                    chart_path = create_data_summary_chart(criteria_count, alternatives_count)
                    db['import_logs'].insert_one({
                        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "criteria_count": criteria_count,
                        "alternatives_count": alternatives_count,
                        "chart": chart_path,
                        "errors": []
                    })
                    logging.info(f"Thêm phương án: {alternative}")
                    flash("Phương án đã được thêm!")
                except Exception as e:
                    logging.error(f"Lỗi thêm phương án: {str(e)}")
                    flash(f"Lỗi khi thêm phương án: {str(e)}")
        
        elif 'delete_criteria' in request.form:
            crit_id = request.form['crit_id']
            crit_name = request.form['crit_name']
            try:
                criteria_collection.delete_one({"_id": ObjectId(crit_id)})
                comparisons_collection.delete_many({"criteria_name": crit_name})
                criteria_count = criteria_collection.count_documents({})
                alternatives_count = alternatives_collection.count_documents({})
                chart_path = create_data_summary_chart(criteria_count, alternatives_count)
                db['import_logs'].insert_one({
                    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "criteria_count": criteria_count,
                    "alternatives_count": alternatives_count,
                    "chart": chart_path,
                    "errors": []
                })
                logging.info(f"Xóa tiêu chí: {crit_name}")
                flash("Tiêu chí đã được xóa! Vui lòng cập nhật ma trận liên quan.")
            except Exception as e:
                logging.error(f"Lỗi xóa tiêu chí: {str(e)}")
                flash(f"Lỗi khi xóa tiêu chí: {str(e)}")
        
        elif 'delete_alternative' in request.form:
            alt_id = request.form['alt_id']
            try:
                alternatives_collection.delete_one({"_id": ObjectId(alt_id)})
                criteria_count = criteria_collection.count_documents({})
                alternatives_count = alternatives_collection.count_documents({})
                chart_path = create_data_summary_chart(criteria_count, alternatives_count)
                db['import_logs'].insert_one({
                    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "criteria_count": criteria_count,
                    "alternatives_count": alternatives_count,
                    "chart": chart_path,
                    "errors": []
                })
                logging.info(f"Xóa phương án: {alt_id}")
                flash("Phương án đã được xóa! Vui lòng cập nhật ma trận liên quan.")
            except Exception as e:
                logging.error(f"Lỗi xóa phương án: {str(e)}")
                flash(f"Lỗi khi xóa phương án: {str(e)}")
        
        elif 'delete_result' in request.form:
            result_id = request.form['result_id']
            try:
                results_collection.delete_one({"_id": ObjectId(result_id)})
                logging.info(f"Xóa kết quả: {result_id}")
                flash("Kết quả đã được xóa!")
            except Exception as e:
                logging.error(f"Lỗi xóa kết quả: {str(e)}")
                flash(f"Lỗi khi xóa kết quả: {str(e)}")
    
    criteria = list(criteria_collection.find())
    alternatives = list(alternatives_collection.find())
    results = list(results_collection.find().sort("timestamp", -1))
    
    latest_import_log = db['import_logs'].find_one(sort=[("timestamp", -1)])
    data_chart = None
    if latest_import_log and 'chart' in latest_import_log:
        data_chart = latest_import_log['chart']
    else:
        criteria_count = len(criteria)
        alternatives_count = len(alternatives)
        if criteria_count > 0 or alternatives_count > 0:
            try:
                chart_path = create_data_summary_chart(criteria_count, alternatives_count)
                db['import_logs'].insert_one({
                    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "criteria_count": criteria_count,
                    "alternatives_count": alternatives_count,
                    "chart": chart_path,
                    "errors": []
                })
                data_chart = chart_path
            except Exception as e:
                logging.error(f"Lỗi tạo biểu đồ mặc định: {str(e)}")
                flash("Lỗi khi tạo biểu đồ tổng quan!")
    
    return render_template('index.html', criteria=criteria, alternatives=alternatives, results=results, data_chart=data_chart)

# Trang nhập ma trận
@app.route('/matrix/<type>/<name>', methods=['GET', 'POST'])
def matrix(type, name):
    if type == 'criteria':
        items = list(criteria_collection.find())
        criteria_name = None
    else:
        items = list(alternatives_collection.find())
        criteria_name = name
    n = len(items)
    if n < 2:
        flash(f"Cần ít nhất 2 {'tiêu chí' if type == 'criteria' else 'phương án'} để so sánh!")
        return redirect(url_for('index'))
    
    item_names = [item['name'] for item in items]
    existing_matrix = comparisons_collection.find_one({"type": type, "criteria_name": criteria_name})
    if existing_matrix and len(existing_matrix['matrix']) == n:
        matrix = np.array(existing_matrix['matrix'])
    else:
        matrix = np.ones((n, n))
    
    if request.method == 'POST':
        try:
            for i in range(n):
                for j in range(i + 1, n):
                    key = f"{i}_{j}"
                    value = request.form.get(key, '1')
                    try:
                        matrix[i][j] = float(value)
                        matrix[j][i] = 1 / float(value)
                    except ValueError:
                        flash(f"Giá trị không hợp lệ tại {item_names[i]} vs {item_names[j]}!")
                        return render_template('matrix.html', type=type, name=name, items=item_names, matrix=matrix.tolist())
            
            weights = calculate_weights(matrix)
            lambda_max, CI, CR = check_consistency(matrix, weights)
            
            if CR >= 0.1:
                flash(f"Ma trận không nhất quán (CR = {CR:.4f} >= 0.1). Vui lòng đánh giá lại!")
                return render_template('matrix.html', type=type, name=name, items=item_names, matrix=matrix.tolist())
            
            comparisons_collection.update_one(
                {"type": type, "criteria_name": criteria_name},
                {
                    "$set": {
                        "matrix": matrix.tolist(),
                        "weights": weights.tolist(),
                        "lambda_max": float(lambda_max),
                        "consistency_index": float(CI),
                        "consistency_ratio": float(CR),
                        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                },
                upsert=True
            )
            logging.info(f"Lưu ma trận {type} cho {criteria_name or 'tiêu chí'}")
            flash(f"Ma trận {'tiêu chí' if type == 'criteria' else f'phương án cho {name}'} đã được lưu!")
            return redirect(url_for('index'))
        except Exception as e:
            logging.error(f"Lỗi lưu ma trận {type}: {str(e)}")
            flash(f"Lỗi khi lưu ma trận: {str(e)}")
            return render_template('matrix.html', type=type, name=name, items=item_names, matrix=matrix.tolist())
    
    return render_template('matrix.html', type=type, name=name, items=item_names, matrix=matrix.tolist())

# Tính toán và hiển thị kết quả
@app.route('/calculate', methods=['POST'])
def calculate():
    criteria = list(criteria_collection.find())
    alternatives = list(alternatives_collection.find())
    
    logging.debug(f"Số tiêu chí: {len(criteria)}, Số phương án: {len(alternatives)}")
    
    if len(criteria) < 2 or len(alternatives) < 2:
        flash("Vui lòng thêm ít nhất 2 tiêu chí và 2 phương án!")
        return redirect(url_for('index'))
    
    criteria_matrix_doc = comparisons_collection.find_one({"type": "criteria"})
    if not criteria_matrix_doc:
        flash("Ma trận so sánh tiêu chí chưa được nhập! Vui lòng nhập ma trận tiêu chí trước.")
        return redirect(url_for('matrix', type='criteria', name='None'))
    
    if len(criteria_matrix_doc['weights']) != len(criteria):
        flash("Kích thước ma trận tiêu chí không khớp với số tiêu chí hiện tại. Vui lòng cập nhật ma trận tiêu chí!")
        return redirect(url_for('matrix', type='criteria', name='None'))
    
    for crit in criteria:
        alt_matrix_doc = comparisons_collection.find_one({"type": "alternatives", "criteria_name": crit['name']})
        if not alt_matrix_doc:
            flash(f"Ma trận phương án cho tiêu chí '{crit['name']}' chưa được nhập!")
            return redirect(url_for('matrix', type='alternatives', name=crit['name']))
        if len(alt_matrix_doc['matrix']) != len(alternatives):
            flash(f"Ma trận phương án cho tiêu chí '{crit['name']}' không khớp với số phương án hiện tại. Vui lòng cập nhật!")
            return redirect(url_for('matrix', type='alternatives', name=crit['name']))
    
    try:
        # Bước 1: Ma trận tiêu chí và trọng số
        criteria_matrix = np.array(criteria_matrix_doc['matrix'])
        criteria_weights = np.array(criteria_matrix_doc['weights'])
        criteria_lambda_max, criteria_CI, criteria_CR = check_consistency(criteria_matrix, criteria_weights)
        criteria_labels = [crit['name'] for crit in criteria]
        criteria_matrix_data = {
            "labels": criteria_labels,
            "matrix": criteria_matrix.tolist(),
            "weights": criteria_weights.tolist(),
            "lambda_max": float(criteria_lambda_max),
            "consistency_index": float(criteria_CI),
            "consistency_ratio": float(criteria_CR),
            "chart": create_criteria_weights_chart(criteria_labels, criteria_weights)
        }
        
        # Bước 2: Ma trận phương án và trọng số
        alternatives_matrices = []
        for crit in criteria:
            alt_matrix_doc = comparisons_collection.find_one({"type": "alternatives", "criteria_name": crit['name']})
            alt_matrix = np.array(alt_matrix_doc['matrix'])
            alt_weights = np.array(alt_matrix_doc['weights'])
            alt_lambda_max, alt_CI, alt_CR = check_consistency(alt_matrix, alt_weights)
            alt_labels = [alt['name'] for alt in alternatives]
            alternatives_matrices.append({
                "criteria_name": crit['name'],
                "labels": alt_labels,
                "matrix": alt_matrix.tolist(),
                "weights": alt_weights.tolist(),
                "lambda_max": float(alt_lambda_max),
                "consistency_index": float(alt_CI),
                "consistency_ratio": float(alt_CR),
                "chart": create_alternatives_weights_chart(crit['name'], alt_labels, alt_weights)
            })
        
        # Bước 3: Tính điểm số
        final_scores = np.zeros(len(alternatives))
        score_details = []
        for i, alt in enumerate(alternatives):
            alt_scores = np.zeros(len(criteria))
            for j, crit in enumerate(criteria):
                alt_matrix_doc = comparisons_collection.find_one({"type": "alternatives", "criteria_name": crit['name']})
                alt_weights = np.array(alt_matrix_doc['weights'])
                alt_scores[j] = alt_weights[i]
            final_score = np.sum(criteria_weights * alt_scores)
            final_scores[i] = final_score
            score_details.append({
                "alternative": alt['name'],
                "scores_per_criteria": alt_scores.tolist(),
                "weighted_scores": (criteria_weights * alt_scores).tolist(),
                "final_score": float(final_score)
            })
        
        # Bước 4: Xếp hạng
        ranking = [{"name": alt['name'], "score": float(score)} for alt, score in zip(alternatives, final_scores)]
        ranking.sort(key=lambda x: x['score'], reverse=True)
        
        # Vẽ biểu đồ xếp hạng
        names = [item['name'] for item in ranking]
        scores = [item['score'] for item in ranking]
        plt.figure(figsize=(8, 5))
        bars = plt.bar(names, scores, color='skyblue')
        plt.title('Xếp hạng phương án', fontsize=14)
        plt.xlabel('Phương án', fontsize=12)
        plt.ylabel('Điểm số', fontsize=12)
        plt.xticks(rotation=45, ha='right')
        for bar in bars:
            yval = bar.get_height()
            plt.text(bar.get_x() + bar.get_width()/2, yval + 0.01, f'{yval:.2f}', ha='center', va='bottom')
        chart_filename = f"ranking_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        chart_path = os.path.join(CHART_DIR, chart_filename)
        plt.savefig(chart_path, bbox_inches='tight')
        plt.close()
        
        result_doc = {
            "_id": ObjectId(),
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "ranking": ranking,
            "chart": os.path.join('charts', chart_filename).replace('\\', '/'),
            "criteria_matrix": criteria_matrix_data,
            "alternatives_matrices": alternatives_matrices,
            "score_details": score_details
        }
        result_id = results_collection.insert_one(result_doc).inserted_id
        logging.info("Tính toán AHP thành công")
        
        # Thêm zip_longest vào render_template để sử dụng trong Jinja2
        return render_template('result.html', ranking=ranking, chart=result_doc['chart'],
                             criteria_matrix=criteria_matrix_data,
                             alternatives_matrices=alternatives_matrices,
                             score_details=score_details, result_id=result_id,
                             zip=zip_longest)
    
    except Exception as e:
        logging.error(f"Lỗi tính toán AHP: {str(e)}")
        flash(f"Lỗi khi tính toán: {str(e)}")
        return redirect(url_for('index'))

# Xuất kết quả ra PDF
@app.route('/export_results/<result_id>', methods=['GET'])
def export_results(result_id):
    try:
        result = results_collection.find_one({"_id": ObjectId(result_id)})
        if not result:
            flash("Kết quả không tồn tại!")
            return redirect(url_for('index'))

        # Chuẩn bị buffer cho PDF
        output = BytesIO()
        doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=0.5*inch, leftMargin=0.5*inch, topMargin=0.5*inch, bottomMargin=0.5*inch)
        elements = []

        # Định dạng kiểu chữ
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            name='Title',
            fontSize=16,
            leading=20,
            alignment=1,  # Căn giữa
            spaceAfter=12,
            fontName='TimesNewRoman'
        )
        heading_style = ParagraphStyle(
            name='Heading',
            fontSize=12,
            leading=16,
            spaceAfter=8,
            fontName='TimesNewRoman'
        )
        normal_style = ParagraphStyle(
            name='Normal',
            fontSize=10,
            leading=12,
            fontName='TimesNewRoman'
        )

        # Tiêu đề báo cáo
        elements.append(Paragraph("Báo Cáo Kết Quả AHP", title_style))
        elements.append(Paragraph(f"Thời gian: {result['timestamp']}", normal_style))
        elements.append(Spacer(1, 0.2*inch))

        # Phần 1: Ma trận tiêu chí
        elements.append(Paragraph("Bước 1: Ma trận tiêu chí và trọng số", heading_style))
        elements.append(Paragraph("Ma trận so sánh tiêu chí thể hiện mức độ ưu tiên giữa các tiêu chí.", normal_style))

        # Ma trận so sánh tiêu chí
        criteria_labels = result['criteria_matrix']['labels']
        criteria_matrix = result['criteria_matrix']['matrix']
        table_data = [[''] + criteria_labels]
        for i, label in enumerate(criteria_labels):
            row = [label] + [f"{val:.4f}" for val in criteria_matrix[i]]
            table_data.append(row)
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'TimesNewRoman'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 0.1*inch))

        # Trọng số tiêu chí
        elements.append(Paragraph("Trọng số tiêu chí", heading_style))
        table_data = [['Tiêu chí', 'Trọng số']]
        for label, weight in zip(criteria_labels, result['criteria_matrix']['weights']):
            table_data.append([label, f"{weight:.4f}"])
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'TimesNewRoman'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 0.1*inch))

        # Chỉ số nhất quán
        elements.append(Paragraph("Chỉ số nhất quán", heading_style))
        elements.append(Paragraph(f"Giá trị riêng lớn nhất (λ_max): {result['criteria_matrix']['lambda_max']:.4f}", normal_style))
        elements.append(Paragraph(f"Chỉ số nhất quán (CI): {result['criteria_matrix']['consistency_index']:.4f}", normal_style))
        elements.append(Paragraph(f"Tỷ lệ nhất quán (CR): {result['criteria_matrix']['consistency_ratio']:.4f}", normal_style))
        elements.append(Spacer(1, 0.1*inch))

        # Biểu đồ trọng số tiêu chí
        chart_path = os.path.join('static', result['criteria_matrix']['chart'])
        if os.path.exists(chart_path):
            elements.append(Paragraph("Biểu đồ trọng số tiêu chí", heading_style))
            elements.append(Image(chart_path, width=4*inch, height=4*inch))
            elements.append(Spacer(1, 0.2*inch))
        else:
            logging.warning(f"Biểu đồ trọng số tiêu chí không tồn tại: {chart_path}")

        # Phần 2: Ma trận phương án
        elements.append(Paragraph("Bước 2: Ma trận phương án theo từng tiêu chí", heading_style))
        for alt_matrix in result['alternatives_matrices']:
            elements.append(Paragraph(f"Tiêu chí: {alt_matrix['criteria_name']}", heading_style))

            # Ma trận so sánh phương án
            alt_labels = alt_matrix['labels']
            alt_matrix_data = alt_matrix['matrix']
            table_data = [[''] + alt_labels]
            for i, label in enumerate(alt_labels):
                row = [label] + [f"{val:.4f}" for val in alt_matrix_data[i]]
                table_data.append(row)
            table = Table(table_data)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), 'TimesNewRoman'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            elements.append(table)
            elements.append(Spacer(1, 0.1*inch))

            # Trọng số phương án
            elements.append(Paragraph("Trọng số phương án", heading_style))
            table_data = [['Phương án', 'Trọng số']]
            for label, weight in zip(alt_labels, alt_matrix['weights']):
                table_data.append([label, f"{weight:.4f}"])
            table = Table(table_data)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), 'TimesNewRoman'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            elements.append(table)
            elements.append(Spacer(1, 0.1*inch))

            # Chỉ số nhất quán
            elements.append(Paragraph("Chỉ số nhất quán", heading_style))
            elements.append(Paragraph(f"Giá trị riêng lớn nhất (λ_max): {alt_matrix['lambda_max']:.4f}", normal_style))
            elements.append(Paragraph(f"Chỉ số nhất quán (CI): {alt_matrix['consistency_index']:.4f}", normal_style))
            elements.append(Paragraph(f"Tỷ lệ nhất quán (CR): {alt_matrix['consistency_ratio']:.4f}", normal_style))
            elements.append(Spacer(1, 0.1*inch))

            # Biểu đồ trọng số phương án
            chart_path = os.path.join('static', alt_matrix['chart'])
            if os.path.exists(chart_path):
                elements.append(Paragraph(f"Biểu đồ trọng số phương án cho tiêu chí: {alt_matrix['criteria_name']}", heading_style))
                elements.append(Image(chart_path, width=5*inch, height=3*inch))
                elements.append(Spacer(1, 0.2*inch))
            else:
                logging.warning(f"Biểu đồ trọng số phương án không tồn tại: {chart_path}")

        # Phần 3: Điểm số chi tiết
        elements.append(Paragraph("Bước 3: Điểm số của từng phương án", heading_style))
        table_data = [['Phương án'] + [f"{crit} (Trọng số: {weight:.4f})" for crit, weight in zip(criteria_labels, result['criteria_matrix']['weights'])] + ['Điểm số cuối cùng']]
        for detail in result['score_details']:
            row = [detail['alternative']]
            for score, weighted_score in zip(detail['scores_per_criteria'], detail['weighted_scores']):
                row.append(f"{score:.4f} (Có trọng số: {weighted_score:.4f})")
            row.append(f"{detail['final_score']:.4f}")
            table_data.append(row)
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'TimesNewRoman'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 0.2*inch))

        # Phần 4: Xếp hạng
        elements.append(Paragraph("Bước 4: Xếp hạng cuối cùng", heading_style))
        table_data = [['Xếp hạng', 'Phương án', 'Điểm số']]
        for i, item in enumerate(result['ranking']):
            table_data.append([str(i + 1), item['name'], f"{item['score']:.4f}"])
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'TimesNewRoman'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 0.1*inch))

        # Biểu đồ xếp hạng
        chart_path = os.path.join('static', result['chart'])
        if os.path.exists(chart_path):
            elements.append(Paragraph("Biểu đồ xếp hạng", heading_style))
            elements.append(Image(chart_path, width=5*inch, height=3*inch))
            elements.append(Spacer(1, 0.2*inch))
        else:
            logging.warning(f"Biểu đồ xếp hạng không tồn tại: {chart_path}")

        # Tạo PDF
        doc.build(elements)
        output.seek(0)

        logging.info(f"Xuất kết quả ra PDF: result_id={result_id}")
        return send_file(
            output,
            download_name=f"ahp_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            as_attachment=True,
            mimetype='application/pdf'
        )

    except Exception as e:
        logging.error(f"Lỗi xuất PDF: {str(e)}")
        flash(f"Lỗi khi xuất kết quả: {str(e)}")
        return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
