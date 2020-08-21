﻿using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using UnityEngine;
using UnityEditor;

public class BrickManager : MonoBehaviour
{
    private List<Brick> _bricks = new List<Brick>();
    private const string _jsonPath = "Assets/Scripts/blueprint.json";

    public List<Brick> UnAssigned
    {
        get { return _bricks.Where(b => !b.Assigned).ToList(); }
    }

    public void InitBlocks(Transform parent, bool setActive)
    {
        foreach (Transform child in parent) {
            DestroyImmediate(child.gameObject);
        }
        int childCount = parent.childCount;
        for (int i = childCount - 1; i>=0; i--)
            DestroyImmediate(parent.transform.GetChild(i).gameObject);
                
        using (StreamReader r = new StreamReader(_jsonPath))
        {
            string json = r.ReadToEnd();
            var d = JsonConvert.DeserializeObject<List<Dictionary<string, string>>>(json);
            foreach (var dict in d)
            {
                var targetPos = new Vector3(float.Parse(dict["transX"]), float.Parse(dict["transY"]), float.Parse(dict["transZ"]));
                var targetRot = new Vector3(0, float.Parse(dict["rotY"]), 0);
                var prefab = Resources.Load($"Prefabs/Blocks/{dict["tag"]}", typeof(GameObject)) as GameObject;
                prefab.transform.position = targetPos;
                prefab.transform.eulerAngles = targetRot;
                prefab.tag = dict["tag"];
                var brickObj = Instantiate(prefab, parent);
                var brick = brickObj.AddComponent<Brick>();
                brick.InitAttribute(0,targetPos,targetRot, parent);  
                brickObj.SetActive(setActive);
                _bricks.Add(brick);
            }
        }
    }
    private void Awake()
    {
        InitBlocks(transform, false);
    }

    public List<string> GetTags()
    {
        var tags = new HashSet<string>();
        foreach (var brick in _bricks)
        {
            tags.Add(brick.tag);
        }
        return tags.ToList();
    }

    public Brick GetNextBrick()
    {
        return UnAssigned[0];
    }

}
