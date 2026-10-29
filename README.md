# SmartIR Tuya Sync

Entité `climate` qui **envoie les codes IR comme SmartIR** (mêmes fichiers de codes, même
contrôleur Broadlink/Xiaomi/etc.) mais dont **l'état réel vient d'une entité `climate` Tuya**
(Tuya Local, Tuya cloud…).

Plus de mode « optimiste » : si la clim est allumée avec la télécommande d'origine, si un
ordre IR est raté ou si le mode est changé depuis l'appli Tuya, l'entité reflète la réalité.

## Prérequis

- [SmartIR](https://github.com/smartHomeHub/SmartIR) installé (ses fichiers de codes et ses contrôleurs sont réutilisés)
- Une entité `climate` Tuya pour le même climatiseur

## Installation (HACS)

1. HACS → ⋮ → *Dépôts personnalisés* → URL de ce repo, catégorie *Intégration*
2. Installer, puis redémarrer Home Assistant

## Configuration

Mêmes options que SmartIR (sans `power_sensor`, devenu inutile), plus `tuya_entity` :

```yaml
climate:
  - platform: smartir_tuya_sync
    name: Clim bureau hybride
    unique_id: clim_bureau_hybride
    device_code: 1320
    controller_data: remote.xxxxx        # comme dans ta config SmartIR actuelle
    tuya_entity: climate.clim_bureau     # source de l'état réel
    humidity_sensor: sensor.xxxxx        # optionnel
    # temperature_sensor: sensor.xxxxx   # optionnel, sinon current_temperature de Tuya
    # sync_delay: 8                      # optionnel, secondes
    # fan_mode_map:                      # optionnel, valeur Tuya -> valeur du fichier IR
    #   medium: high
```

## Fonctionnement

| Élément | Source |
|---|---|
| Commandes (mode, température, ventilation, swing) | codes IR SmartIR |
| `hvac_mode`, température cible, `fan_mode`, swing | état de `tuya_entity` |
| `current_temperature` | `temperature_sensor` si défini, sinon `tuya_entity` |
| `current_humidity` | `humidity_sensor` si défini |

- `fan_mode_map` permet de forcer une correspondance (ex. Tuya remonte `medium` quand on envoie `high`).
- Les valeurs Tuya sont converties vers le vocabulaire du fichier IR (`medium` → `mid`, `strong` → `high`…).
- Aucun code IR n'est jamais envoyé à cause d'un changement d'état Tuya (pas de boucle).
- Après un ordre IR, les retours Tuya sont ignorés pendant `sync_delay` secondes (le temps que
  la clim applique le code), puis l'état est réaligné sur Tuya. Si l'ordre n'a pas fonctionné,
  l'entité revient donc à l'état réel.
- Modes Tuya sans équivalent IR (`heat_cool`, `fan_only`) : le dernier mode IR connu est conservé.
- Si l'entité Tuya est `unavailable`/`unknown`, le dernier état connu est conservé.
