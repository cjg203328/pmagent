"""
OCR图片识别 - 集成PaddleOCR
"""
from typing import Dict, List, Optional
import numpy as np
from PIL import Image
import re

try:
    from paddleocr import PaddleOCR
    PADDLEOCR_AVAILABLE = True
except ImportError:
    PADDLEOCR_AVAILABLE = False
    print("Warning: PaddleOCR not installed. Run: pip install paddleocr")


class OCRImageParser:
    """OCR图片识别解析器"""

    def __init__(self, lang='ch'):
        """
        初始化OCR引擎

        Args:
            lang: 语言，'ch'（中文）, 'en'（英文）
        """
        if not PADDLEOCR_AVAILABLE:
            raise ImportError("PaddleOCR is not installed")

        # 初始化PaddleOCR
        self.ocr = PaddleOCR(
            use_angle_cls=True,  # 使用方向分类器
            lang=lang,
            use_gpu=False,  # 默认CPU，如有GPU可改为True
            show_log=False
        )

    def recognize(self, image_path: str) -> Dict:
        """
        识别图片中的文字

        Args:
            image_path: 图片路径

        Returns:
            识别结果
        """
        try:
            # 读取图片
            image = Image.open(image_path)
            img_array = np.array(image)

            # OCR识别
            result = self.ocr.ocr(img_array, cls=True)

            # 提取文本和置信度
            texts = []
            boxes = []
            confidences = []

            if result and result[0]:
                for line in result[0]:
                    box = line[0]  # 坐标
                    text_info = line[1]  # (文本, 置信度)

                    texts.append(text_info[0])
                    boxes.append(box)
                    confidences.append(text_info[1])

            # 合并文本
            full_text = "\n".join(texts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0

            return {
                "success": True,
                "text": full_text,
                "lines": texts,
                "boxes": boxes,
                "confidence": avg_confidence,
                "line_count": len(texts),
                "image_info": {
                    "path": image_path,
                    "size": image.size,
                    "mode": image.mode
                }
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }

    def recognize_table(self, image_path: str) -> Dict:
        """
        识别表格结构（报价单）

        Args:
            image_path: 图片路径

        Returns:
            表格识别结果
        """
        # 先进行普通OCR
        ocr_result = self.recognize(image_path)

        if not ocr_result["success"]:
            return ocr_result

        # 分析表格结构
        lines = ocr_result["lines"]
        boxes = ocr_result["boxes"]

        # 按Y坐标分组（识别行）
        rows = self._group_by_rows(lines, boxes)

        # 识别表格数据
        table_data = self._parse_table_data(rows)

        return {
            "success": True,
            "text": ocr_result["text"],
            "table_data": table_data,
            "row_count": len(rows),
            "confidence": ocr_result["confidence"]
        }

    def _group_by_rows(self, texts: List[str], boxes: List) -> List[Dict]:
        """按行分组文本"""
        rows = []

        # 计算每个文本的Y中心坐标
        items = []
        for text, box in zip(texts, boxes):
            y_center = (box[0][1] + box[2][1]) / 2
            x_left = box[0][0]
            items.append({
                "text": text,
                "y": y_center,
                "x": x_left,
                "box": box
            })

        # 按Y坐标排序
        items.sort(key=lambda x: x["y"])

        # 分组（Y坐标相近的归为一行）
        threshold = 20  # Y坐标差异阈值

        current_row = []
        current_y = items[0]["y"] if items else 0

        for item in items:
            if abs(item["y"] - current_y) < threshold:
                current_row.append(item)
            else:
                # 新行
                if current_row:
                    # 按X坐标排序（同一行内从左到右）
                    current_row.sort(key=lambda x: x["x"])
                    rows.append(current_row)

                current_row = [item]
                current_y = item["y"]

        # 添加最后一行
        if current_row:
            current_row.sort(key=lambda x: x["x"])
            rows.append(current_row)

        return rows

    def _parse_table_data(self, rows: List[List[Dict]]) -> Dict:
        """解析表格数据"""
        if not rows:
            return {}

        # 查找表头
        header_row = None
        data_start = 0

        for i, row in enumerate(rows):
            row_text = " ".join([item["text"] for item in row])
            if any(keyword in row_text for keyword in ["名称", "数量", "单价", "总价", "金额"]):
                header_row = row
                data_start = i + 1
                break

        if not header_row:
            return {"rows": []}

        # 提取表头
        headers = [item["text"] for item in header_row]

        # 提取数据行
        data_rows = []
        for row in rows[data_start:]:
            # 跳过合计行
            row_text = " ".join([item["text"] for item in row])
            if any(keyword in row_text for keyword in ["合计", "总计", "小计"]):
                break

            # 提取数据
            row_data = {}
            for j, item in enumerate(row):
                header = headers[j] if j < len(headers) else f"col_{j}"
                row_data[header] = item["text"]

            data_rows.append(row_data)

        return {
            "headers": headers,
            "rows": data_rows,
            "row_count": len(data_rows)
        }

    def parse_quote_from_ocr(self, ocr_result: Dict) -> Dict:
        """从OCR结果中提取报价单信息"""
        if not ocr_result["success"]:
            return {"success": False, "error": "OCR识别失败"}

        text = ocr_result["text"]
        table_data = ocr_result.get("table_data", {})

        # 提取关键信息
        quote_info = {
            "success": True,
            "client": self._extract_client(text),
            "project_name": self._extract_project_name(text),
            "date": self._extract_date(text),
            "contact": self._extract_contact(text),
            "assets": self._extract_assets(table_data),
            "total_amount": self._extract_total_amount(text),
            "raw_text": text
        }

        return quote_info

    def _extract_client(self, text: str) -> Optional[str]:
        """提取客户名称"""
        patterns = [
            r'客户[：:]\s*([^\n]+)',
            r'甲方[：:]\s*([^\n]+)',
            r'(腾讯|网易|米哈游|字节|完美世界)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()

        return None

    def _extract_project_name(self, text: str) -> Optional[str]:
        """提取项目名称"""
        patterns = [
            r'项目名称[：:]\s*([^\n]+)',
            r'项目[：:]\s*([^\n]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()

        return None

    def _extract_date(self, text: str) -> Optional[str]:
        """提取日期"""
        patterns = [
            r'日期[：:]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})',
            r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()

        return None

    def _extract_contact(self, text: str) -> Optional[str]:
        """提取联系人"""
        patterns = [
            r'联系人[：:]\s*([^\n]+)',
            r'对接人[：:]\s*([^\n]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()

        return None

    def _extract_assets(self, table_data: Dict) -> List[Dict]:
        """从表格数据中提取资产"""
        if not table_data or "rows" not in table_data:
            return []

        assets = []
        for row in table_data["rows"]:
            asset = {
                "name": None,
                "quantity": None,
                "unit_price": None,
                "total": None
            }

            # 查找各字段
            for key, value in row.items():
                if "名称" in key or "内容" in key:
                    asset["name"] = value
                elif "数量" in key:
                    asset["quantity"] = self._extract_number(value)
                elif "单价" in key:
                    asset["unit_price"] = self._extract_number(value)
                elif "总价" in key or "金额" in key:
                    asset["total"] = self._extract_number(value)

            if asset["name"]:
                assets.append(asset)

        return assets

    def _extract_total_amount(self, text: str) -> Optional[float]:
        """提取总金额"""
        patterns = [
            r'合计[：:]\s*[¥￥]?\s*([\d,]+\.?\d*)',
            r'总计[：:]\s*[¥￥]?\s*([\d,]+\.?\d*)',
            r'总价[：:]\s*[¥￥]?\s*([\d,]+\.?\d*)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return self._extract_number(match.group(1))

        return None

    def _extract_number(self, text: str) -> Optional[float]:
        """从文本中提取数字"""
        if not text:
            return None

        try:
            # 移除货币符号和逗号
            text = text.replace(",", "").replace("¥", "").replace("￥", "").strip()
            return float(text)
        except (TypeError, ValueError):
            return None


# 使用示例
if __name__ == "__main__":
    if PADDLEOCR_AVAILABLE:
        # 初始化OCR
        ocr_parser = OCRImageParser(lang='ch')

        # 识别图片
        result = ocr_parser.recognize_table("./test_data/报价单截图.png")

        if result["success"]:
            print(f"识别成功！置信度: {result['confidence']:.2f}")
            print(f"\n识别文本:\n{result['text']}")

            # 解析报价单
            quote = ocr_parser.parse_quote_from_ocr(result)
            print(f"\n客户: {quote['client']}")
            print(f"项目: {quote['project_name']}")
            print(f"总金额: ¥{quote['total_amount']}")
        else:
            print(f"识别失败: {result['error']}")
    else:
        print("请先安装PaddleOCR: pip install paddleocr")
