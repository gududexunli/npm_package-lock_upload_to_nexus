import json
import os
import requests
import configparser
from tqdm import tqdm


class NexusUploader:
    """封装Nexus API v1 操作"""

    def __init__(self, config):
        nexus_cfg = config['Nexus']
        self.base_url = nexus_cfg.get('base_url').rstrip('/')
        self.auth = (nexus_cfg.get('username'), nexus_cfg.get('password'))
        self.upload_repo = nexus_cfg.get('upload_repository')

        repos = nexus_cfg.get('check_repositories', self.upload_repo)
        self.check_repos = [r.strip() for r in repos.split(',')]

        self.session = requests.Session()
        self.session.auth = self.auth
        # 允许上传的API
        self.upload_url = f"{self.base_url}/service/rest/v1/components?repository={self.upload_repo}"
        # 搜索/删除组件的API
        self.components_url = f"{self.base_url}/service/rest/v1/components"
        self.search_components_url = f"{self.base_url}/service/rest/v1/search"

        print(f"Nexus Uploader 初始化: URL={self.base_url}, UploadRepo={self.upload_repo}")
        print(f"将检查以下仓库: {self.check_repos}")

    def _find_component(self, repo, group, name, version):
        """在指定仓库中查找组件"""
        params = {
            'repository': repo,
            'name': name,
            'version': version
        }
        if group:
            params['group'] = group.replace('@', '')
        else:
            params['group'] = '""'

        try:
            r = self.session.get(self.search_components_url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get('items'):
                if len(data['items']) != 1:
                    raise ValueError(f'返回结果不唯一,group:{group},name:{name}, version:{version}')
                return data['items'][0]  # 返回第一个匹配项
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            print(f"!! 查找组件时出错 ({repo}): {e}")
        except Exception as e:
            print(f"!! 查找组件时发生意外错误 ({repo}): {e}")
        return None

    def _get_remote_sha512_hex(self, component):
        """从组件信息中提取 .tgz 资产的 sha512 (hex)"""
        if not component or 'assets' not in component:
            return None

        for asset in component.get('assets', []):
            # 确保我们拿到的是 .tgz 文件的 checksum
            if asset.get('path', '').endswith('.tgz'):
                checksums = asset.get('checksum', {})
                # Nexus API 返回的是 hex 格式
                return checksums.get('sha512')
        return None

    def _delete_component(self, component_id):
        """按ID删除组件"""
        delete_url = f"{self.components_url}/{component_id}"
        try:
            r = self.session.delete(delete_url, timeout=30)
            r.raise_for_status()
            print(f"    -> 成功删除旧组件 (ID: {component_id})")
            return True
        except Exception as e:
            print(f"    -> !! 删除组件 {component_id} 失败: {e}")
            return False

    def _upload_package(self, package_meta):
        """上传 .tgz 文件"""
        local_path = package_meta['local_path']
        if not os.path.exists(local_path):
            print(f"    -> !! 文件 {local_path} 不存在，跳过上传。")
            return False

        # Nexus API v1 (npm) 需要 'npm.asset' 作为文件字段名
        files = {
            'npm.asset': (os.path.basename(local_path), open(local_path, 'rb'), 'application/x-gzip')
        }

        try:
            r = self.session.post(self.upload_url, files=files, timeout=300)  # 上传可能需要更长时间
            r.raise_for_status()
            print(f"    -> 成功上传: {package_meta['name']}@{package_meta['version']}")
            return True
        except Exception as e:
            print(f"    -> !! 上传 {package_meta['name']} 失败: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"    -> 响应: {e.response.text}")
            return False

    def process_package(self, package_meta):
        """
        处理单个包：检查、对比、删除、上传
        """
        group = package_meta['group']
        name = package_meta['name']
        version = package_meta['version']
        local_sha512_hex = package_meta['sha512_hex']

        pkg_id = f"{group}/{name}@{version}" if group else f"{name}@{version}"
        print(f"--- 正在处理: {pkg_id} ---")

        found_component = None
        sha_matches = False

        # 1. 检查所有 'check_repositories'
        for repo in self.check_repos:
            component = self._find_component(repo, group, name, version)
            if component:
                print(f"    -> 在仓库 '{repo}' 中找到。")
                found_component = component
                remote_sha512_hex = self._get_remote_sha512_hex(component)

                if remote_sha512_hex == local_sha512_hex:
                    print("    -> SHA-512 匹配。跳过。")
                    sha_matches = True
                else:
                    print(
                        f"    -> SHA-512 不匹配! (本地: {local_sha512_hex[:10]}... / 远程: {str(remote_sha512_hex)[:10]}...)")

                break  # 只要在一个仓库找到就停止搜索

        # 2. 如果已找到且 SHA 匹配，则跳过
        if sha_matches:
            return

        # 3. 如果找到但不匹配 (SHA mismatch)
        if found_component and not sha_matches:
            # 只有当这个包位于 *我们即将上传的仓库* 时，我们才删除它
            if found_component['repository'] == self.upload_repo:
                print(f"    -> 在目标仓库 '{self.upload_repo}' 中发现不匹配的包，正在删除...")
                self._delete_component(found_component['id'])
            else:
                print(
                    f"    -> 在仓库 '{found_component['repository']}' 中发现不匹配的包，但目标仓库是 '{self.upload_repo}'。")
                print("    -> 将继续上传到目标仓库 (可能导致重复，请检查Nexus配置)。")
                # self._delete_component(found_component['id'])

        # 4. 上传 (如果未找到，或已删除旧的)
        print(f"    -> 准备上传到 '{self.upload_repo}'...")
        self._upload_package(package_meta)


def load_config():
    """加载 config.ini 配置"""
    config = configparser.ConfigParser()
    if not os.path.exists('config.ini'):
        raise FileNotFoundError("未找到 config.ini 配置文件。")
    config.read('config.ini', 'utf-8')
    return config


def main_upload():
    """主上传函数"""
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(e)
        return

    meta_file = config.get('Downloader', 'meta_file', fallback='meta-info.json')

    try:
        with open(meta_file, 'r', encoding='utf-8') as f:
            meta_info_list = json.load(f)
    except FileNotFoundError:
        print(f"错误: 元数据文件 {meta_file} 未找到。")
        print("请先运行 '下载 npm 包' (选项 1)。")
        return
    except json.JSONDecodeError:
        print(f"错误: 元数据文件 {meta_file} 格式损坏。")
        return

    if not meta_info_list:
        print("元数据文件为空，没有需要上传的包。")
        return

    print(f"加载了 {len(meta_info_list)} 个包的元数据，开始上传到 Nexus...")

    try:
        uploader = NexusUploader(config)
    except Exception as e:
        print(f"初始化 Nexus Uploader 失败: {e}")
        return

    # 这里我们使用 for 循环而不是并行，因为上传/删除操作有依赖关系，
    # 并且 Nexus 可能不喜欢高并发的 API 写入。
    # 如果您有大量包，可以考虑改成 ThreadPoolExecutor，但要小心 API 速率限制。
    for package_meta in tqdm(meta_info_list, desc="上传进度"):
        try:
            uploader.process_package(package_meta)
        except Exception as e:
            print(f"!! 处理 {package_meta.get('name', '未知包')} 时发生严重错误: {e}")

    print("\n--- 上传过程完成 ---")


if __name__ == "__main__":
    main_upload()