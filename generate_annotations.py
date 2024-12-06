import os
import csv
import re
from http import HTTPStatus
from dashscope import Generation
import dashscope
# 设置Dashscope API密钥
dashscope.api_key = "sk-704e7a4155304f00abb5d8105ea13c7d"



def read_table_descriptions(description_folder):
    """
    读取database_description文件夹中的所有表描述文件，提取列描述信息。
    """
    table_descriptions = {}
    for filename in os.listdir(description_folder):
        filepath = os.path.join(description_folder, filename)
        print(filename)
        if os.path.isfile(filepath) and filename.endswith('.csv'):
            table_name = os.path.splitext(filename)[0]
            with open(filepath, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                columns = []
                table_description = ""
                for row in reader:
                    if 'column_description' in row and row['column_description']:
                        columns.append({
                            'column_name': row.get('original_column_name', row.get('column_name', '')).strip(),
                            'description': row['column_description'].strip()
                        })
                    if 'table_description' in row and row['table_description']:
                        table_description = row['table_description'].strip()
                table_descriptions[table_name] = {
                    'table_description': table_description,
                    'columns': columns
                }
    return table_descriptions

def read_schema_file(schema_file_path):
    """
    读取schema文件，提取每个表的字段名称和数据类型。
    """
    with open(schema_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取CREATE TABLE语句
    tables = re.findall(r'CREATE TABLE\s+`?\"?(\w+)`?\"?\s*\((.*?)\);', content, re.DOTALL)
    schema_info = {}
    for table_name, columns_str in tables:
        # 处理字段定义，考虑可能的逗号、约束等
        columns_list = []
        columns_definitions = columns_str.split(',')
        for column_def in columns_definitions:
            match = re.match(r'\s*`?\"?(\w+)`?\"?\s+([^\s,]+)', column_def.strip())
            if match:
                column_name = match.group(1)
                data_type = match.group(2)
                columns_list.append((column_name, data_type))
        schema_info[table_name] = columns_list
    return schema_info

def read_dump_file(dump_file_path):
    """
    读取dump文件，提取每个表的示例数据。
    """
    with open(dump_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取INSERT INTO语句
    inserts = re.findall(r'INSERT INTO\s+`?\"?(\w+)`?\"?\s+VALUES\s*(.*?);', content, re.DOTALL)
    dump_data = {}
    for table_name, values_str in inserts:
        # 分割多个值的插入
        values_list = re.findall(r'\((.*?)\)', values_str)
        for values in values_list:
            # 使用CSV解析，处理逗号和引号
            reader = csv.reader([values], delimiter=',', quotechar="'", escapechar='\\')
            row = next(reader)
            if table_name not in dump_data:
                dump_data[table_name] = []
            dump_data[table_name].append(row)
    return dump_data

def map_data_format(data_type):
    """
    将数据库的数据类型映射为data_format。
    """
    data_type = data_type.lower()
    if any(t in data_type for t in ['int', 'integer', 'tinyint', 'smallint', 'bigint']):
        return 'int'
    elif any(t in data_type for t in ['char', 'text', 'varchar', 'nvarchar', 'nchar', 'string']):
        return 'text'
    elif any(t in data_type for t in ['float', 'double', 'decimal', 'real', 'numeric']):
        return 'float'
    elif any(t in data_type for t in ['date', 'time', 'year', 'timestamp', 'datetime']):
        return 'date'
    else:
        return 'text'

def generate_prompt(table_name, table_description, columns, sample_data, table_descriptions):
    """
    根据标注要求，生成发送给OpenAI模型的提示（Prompt）。
    """
    prompt = f"### 标注要求：\n" \
             f"请根据以下信息，按照标注要求生成表的注释。\n\n" \
             f"表名称：{table_name}\n" \
             f"表描述：{table_description}\n\n" \
             f"字段信息：\n"

    for idx, (column_name, data_type) in enumerate(columns):
        data_format = map_data_format(data_type)
        example_value = sample_data[idx] if sample_data and idx < len(sample_data) else ''
        existing_description = ""
        if table_name in table_descriptions and table_descriptions[table_name]['columns']:
            for col in table_descriptions[table_name]['columns']:
                if col['column_name'] == column_name:
                    existing_description = col['description']
                    break
        prompt += f"- 字段名：{column_name}，数据类型：{data_type}，数据格式：{data_format}，现有描述：{existing_description}，示例数据：{example_value}\n"

    prompt += "\n#### 标注要求：\n" \
              "1. table_description：数据表描述，例如主要用来干什么，记录的主要内容涵盖哪些方面等。\n" \
              "2. column_description：数据字段描述，主要解释字段的含义，赋予语义内容。\n" \
              "3. data_format：数据格式描述，注意：不是数据字段创建的类型定义，而是直观的人看起来存储的内容格式的总结，\n" \
              "   例如：text（可能包含数据库定义中的varchar，text，char等），int（可能包含数据库定义中的integer，int，tinyint等），\n" \
              "   date（可能包含数据库定义中的time, year, timestamp, date等），float（可能包含数据库定义中的float，double，decimal等）。\n" \
              "4. value_description：数据字段值具体存储内容的总结与详细描述，需要参照示例数据，分析并总结。\n" \
              "5. example_data：样例数据，取自示例数据。\n\n" \
              "请按照以下格式输出：\n" \
              "表描述：<填写表描述>\n" \
              "字段注释：\n" \
              "- 字段名：<字段名>，字段描述：<字段描述>，数据格式：<数据格式>，值描述：<值描述>，示例数据：<示例数据>\n" \
              "... \n"

    return prompt


def call_dashscope_api(prompt):
    """
    调用OpenAI的API，获取模型生成的注释。
    """

    messages=[
        {"role": "system", "content": "你是一个数据库专家，熟悉数据库设计和数据分析。"},
        {"role": "user", "content": prompt}
    ]
    response = Generation.call(model="qwen2-72b-instruct",
                               messages=messages,
                               # 将输出设置为"message"格式
                               result_format='message')
    if response.status_code == HTTPStatus.OK:
        output = response.output.choices[0].message.content
        print(output)
    else:
        print('Request id: %s, Status code: %s, error code: %s, error message: %s' % (
            response.request_id, response.status_code,
            response.code, response.message
        ))

    return response.output.choices[0].message.content

def parse_model_response(response_text):
    """
    解析模型的响应，提取表描述和字段注释。
    """
    table_description = ''
    columns_annotations = []

    lines = response_text.strip().split('\n')
    current_section = None

    for line in lines:
        line = line.strip()
        if line.startswith('表描述：'):
            current_section = 'table_description'
            table_description = line.replace('表描述：', '').strip()
        elif line.startswith('字段注释：'):
            current_section = 'columns'
        elif current_section == 'columns' and line.startswith('- 字段名：'):
            # 提取字段信息
            pattern = r'- 字段名：(.+?)，字段描述：(.+?)，数据格式：(.+?)，值描述：(.+?)，示例数据：(.+)'
            match = re.match(pattern, line)
            if match:
                column_info = {
                    'original_column_name': match.group(1).strip(),
                    'column_description': match.group(2).strip(),
                    'data_format': match.group(3).strip(),
                    'value_description': match.group(4).strip(),
                    'example_data': match.group(5).strip(),
                }
                columns_annotations.append(column_info)
    return table_description, columns_annotations

def main():
    # 设置路径和文件名
    db_id = 0
    db_name = 'european_football_2'
    description_folder = 'european_football_2/database_description'
    schema_file_path = 'european_football_2/european_football_2_schema_why.txt'
    dump_file_path = 'european_football_2/european_football_2_dump_why.txt'
    output_csv = 'annotations.csv'

    # 读取描述文件
    table_descriptions = read_table_descriptions(description_folder)

    # 读取schema文件
    schema_info = read_schema_file(schema_file_path)

    # 读取dump文件
    dump_data = read_dump_file(dump_file_path)

    # 打开CSV文件写入
    with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['db_id', 'db_name', 'table_name', 'table_description',
                      'original_column_name', 'column_description', 'data_format',
                      'value_description', 'example_data']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        # 遍历每个表
        for table_name in schema_info:
            columns_in_schema = schema_info[table_name]
            dump_samples = dump_data.get(table_name, [])
            sample_row = dump_samples[0] if dump_samples else []
            sample_data = sample_row

            # 获取表描述
            table_desc_records = table_descriptions.get(table_name, {})
            table_description = table_desc_records.get('table_description', '')

            # 生成Prompt
            prompt = generate_prompt(table_name, table_description, columns_in_schema, sample_data, table_descriptions)

            # 调用Dashscope API获取注释
            model_response = call_dashscope_api(prompt)

            # 解析模型的响应
            table_description_generated, columns_annotations = parse_model_response(model_response)

            # 写入CSV文件
            first_row = True
            for idx, column_info in enumerate(columns_annotations):
                row = {
                    'db_id': db_id if first_row else '',
                    'db_name': db_name if first_row else '',
                    'table_name': table_name if first_row else '',
                    'table_description': table_description_generated if first_row else '',
                    'original_column_name': column_info.get('original_column_name', ''),
                    'column_description': column_info.get('column_description', ''),
                    'data_format': column_info.get('data_format', ''),
                    'value_description': column_info.get('value_description', ''),
                    'example_data': column_info.get('example_data', ''),
                }
                writer.writerow(row)
                first_row = False

    print(f"注释文件已生成：{output_csv}")

if __name__ == '__main__':
    main()