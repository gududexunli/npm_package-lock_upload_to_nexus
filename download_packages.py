import json
import os
import requests
import hashlib
import base64
import configparser
import concurrent.futures
from tqdm import tqdm
from urllib.parse import urlparse, urljoin
from pathlib import Path


def load_config():
    """加载 config.ini 配置"""
    config = configparser.ConfigParser()
    if not os.path.exists('config.ini'):
        raise FileNotFoundError("未找到 config.ini 配置文件。")
    config.read('config.ini', 'utf-8')
    return config


def parse_package_lock(lockfile_path):
    """
    解析 package-lock.json (v2/v3) 并提取 'packages' 部分。
    """
    print(f"正在解析 {lockfile_path}...")
    packages_to_download = []
    try:
        with open(lockfile_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: {lockfile_path} 未找到。")
        return []
    except json.JSONDecodeError:
        print(f"错误: {lockfile_path} 文件格式不正确。")
        return []

    all_packages = data.get('packages', {})
    if not all_packages:
        print("警告: 'packages' 字段为空或未找到。这可能是一个 v1 格式的 lockfile，此脚本仅支持 v2/v3。")
        return []

    for path, details in all_packages.items():
        # 跳过根项目 (path == "")
        if not path:
            continue

        resolved = details.get('resolved')
        integrity = details.get('integrity')
        version = details.get('version')

        # 从 'path' 中推断 'name'
        # e.g., "node_modules/@angular/core" -> "@angular/core"
        name = path[path.rfind('node_modules/') + len('node_modules/'):]

        if not all([resolved, integrity, version, name]):
            print(f"跳过无效条目: {path}")
            continue

        # 提取 sha512
        sha512_b64 = None
        for s in integrity.split(' '):
            if s.startswith('sha512-'):
                sha512_b64 = s.replace('sha512-', '')
                break

        if not sha512_b64:
            print(f"警告: 未找到 {name}@{version} 的 sha512 值，跳过。")
            continue

        packages_to_download.append({
            'name': name,
            'version': version,
            'resolved': resolved,
            'sha512_b64': sha512_b64
        })

    print(f"解析完成，共找到 {len(packages_to_download)} 个依赖包。")
    return packages_to_download


def download_package(package_details, config):
    """
    下载单个包，验证 checksum，并返回元数据。
    """
    name = package_details['name']
    version = package_details['version']
    original_url = package_details['resolved']
    expected_sha512_b64 = package_details['sha512_b64']

    downloader_cfg = config['Downloader']
    download_dir = downloader_cfg.get('download_dir', 'npm_tgz')
    use_resolved = downloader_cfg.getboolean('use_resolved_url', True)
    mirror = downloader_cfg.get('mirror_registry')

    # 确定下载URL
    download_url = original_url
    if not use_resolved and mirror:
        # 强制使用镜像源，需要根据 'name' 和 'version' 重建 URL
        # e.g., @angular/core -> @angular/core/-/core-15.0.0.tgz
        # e.g., react -> react/-/react-18.0.0.tgz
        pkg_filename_base = name.split('/')[-1]
        pkg_filename = f"{pkg_filename_base}-{version}.tgz"
        # 使用 urljoin 来正确处理 /
        download_url = urljoin(mirror, f"{name}/-/{pkg_filename}")

    # 确定本地文件名和路径
    # 我们使用规范化的名称（非作用域部分 + 版本）作为文件名
    pkg_filename_base = name.replace('/', '#').replace('\\', '#')
    pkg_filename = f"{pkg_filename_base}-{version}.tgz"
    tgz_path = os.path.join(download_dir, pkg_filename)

    # 确保目录存在
    Path(download_dir).mkdir(exist_ok=True)

    try:
        downloaded_hash = hashlib.sha512()
        if os.path.exists(tgz_path):
            with open(tgz_path, 'rb') as f:
                # 循环读取文件块
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        # 读取完毕，跳出循环
                        break
                    # 更新哈希值
                    downloaded_hash.update(chunk)
        else:
            # 下载
            r = requests.get(download_url, stream=True, timeout=60)
            r.raise_for_status()
            with open(tgz_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded_hash.update(chunk)

        # 验证
        downloaded_hash_b64 = base64.b64encode(downloaded_hash.digest()).decode('utf-8')
        downloaded_hash_hex = downloaded_hash.hexdigest()

        if downloaded_hash_b64 != expected_sha512_b64:
            os.remove(tgz_path)  # 删除坏文件
            raise ValueError(f"SHA-512 校验失败! 预期: {expected_sha512_b64}, 得到: {downloaded_hash_b64}")

        # 准备元数据
        # e.g., name: @angular/core -> group: @angular, name: core
        group = ''
        pkg_name = name
        if '/' in name:
            group, pkg_name = name.split('/', 1)

        # Nexus 搜索时通常使用 "name-version" 格式, e.g., "react-18.2.0"
        nexus_search_name = f"{pkg_name}-{version}"

        meta_entry = {
            'group': group,
            'name': pkg_name,
            'version': version,
            'nexus_search_name': nexus_search_name,
            'download_url': download_url,
            'local_path': os.path.abspath(tgz_path),
            'sha512_hex': downloaded_hash_hex  # 存储 hex 用于 Nexus API 比较
        }
        return meta_entry

    except Exception as e:
        print(f"!! 下载 {name}@{version} 失败: {e}")
        return None


def main_download():
    """主下载函数"""
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(e)
        return

    packages = parse_package_lock('package-lock.json')
    if not packages:
        print("没有找到要下载的包。")
        return

    meta_info_list = []
    max_workers = config.getint('Downloader', 'max_workers', fallback=10)

    print(f"开始并行下载，最大线程数: {max_workers}...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交任务
        futures = [executor.submit(download_package, pkg, config) for pkg in packages]

        # 使用 tqdm 显示进度
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc="下载进度"):
            try:
                result = future.result()
                if result:
                    meta_info_list.append(result)
            except Exception as e:
                # 这个异常理论上应该在 download_package 内部被捕获
                print(f"!! 处理任务时发生意外错误: {e}")

    meta_file = config.get('Downloader', 'meta_file', fallback='meta-info.json')
    try:
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta_info_list, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"!! 写入元数据文件 {meta_file} 失败: {e}")
        return

    print("\n--- 下载完成 ---")
    print(f"成功下载 {len(meta_info_list)} / {len(packages)} 个包。")
    print(f"元数据已保存到: {meta_file}")
    print(f"Tgz 文件已保存到: {config.get('Downloader', 'download_dir')}")


if __name__ == "__main__":
    main_download()