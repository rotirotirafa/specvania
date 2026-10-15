/** Transitional T15 pixels: area tiles and HUD hearts, coins, stamina pips.
 * Party, monsters, bosses and doors now resolve through cast.js to Kenney art.
 * Keep these original pixels until their replacing room updates every caller.
 */
function freezeSprites(sprites) {
  for (const states of Object.values(sprites)) {
    for (const frames of Object.values(states)) {
      for (const frame of frames) Object.freeze(frame);
      Object.freeze(frames);
    }
    Object.freeze(states);
  }
  return Object.freeze(sprites);
}

export const SPRITES = freezeSprites({
  "room_a1": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KGGGGGGGGGGGGGGK",
        "KGSSSSSSSSSSSSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSKKKKKKKKKKSGK",
        "KGSSSSSSSSSSSSGK",
        "KGGGGGGGGGGGGGGK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a2": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KBBBBBBBBBBBBBBK",
        "KBSSSSSSSSSSSSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSKKKKKKKKKKSBK",
        "KBSSSSSSSSSSSSBK",
        "KBBBBBBBBBBBBBBK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a3": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KVVVVVVVVVVVVVVK",
        "KVSSSSSSSSSSSSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSKKKKKKKKKKSVK",
        "KVSSSSSSSSSSSSVK",
        "KVVVVVVVVVVVVVVK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a4": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KRRRRRRRRRRRRRRK",
        "KRSSSSSSSSSSSSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSKKKKKKKKKKSRK",
        "KRSSSSSSSSSSSSRK",
        "KRRRRRRRRRRRRRRK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a5": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KOOOOOOOOOOOOOOK",
        "KOSSSSSSSSSSSSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSKKKKKKKKKKSOK",
        "KOSSSSSSSSSSSSOK",
        "KOOOOOOOOOOOOOOK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a6": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KYYYYYYYYYYYYYYK",
        "KYSSSSSSSSSSSSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSKKKKKKKKKKSYK",
        "KYSSSSSSSSSSSSYK",
        "KYYYYYYYYYYYYYYK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a7": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KCCCCCCCCCCCCCCK",
        "KCSSSSSSSSSSSSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSKKKKKKKKKKSCK",
        "KCSSSSSSSSSSSSCK",
        "KCCCCCCCCCCCCCCK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a8": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KNNNNNNNNNNNNNNK",
        "KNSSSSSSSSSSSSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSKKKKKKKKKKSNK",
        "KNSSSSSSSSSSSSNK",
        "KNNNNNNNNNNNNNNK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a9": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KWWWWWWWWWWWWWWK",
        "KWSSSSSSSSSSSSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSKKKKKKKKKKSWK",
        "KWSSSSSSSSSSSSWK",
        "KWWWWWWWWWWWWWWK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a10": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KPPPPPPPPPPPPPPK",
        "KPSSSSSSSSSSSSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSKKKKKKKKKKSPK",
        "KPSSSSSSSSSSSSPK",
        "KPPPPPPPPPPPPPPK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a11": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KLLLLLLLLLLLLLLK",
        "KLSSSSSSSSSSSSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSKKKKKKKKKKSLK",
        "KLSSSSSSSSSSSSLK",
        "KLLLLLLLLLLLLLLK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a12": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KHHHHHHHHHHHHHHK",
        "KHSSSSSSSSSSSSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSKKKKKKKKKKSHK",
        "KHSSSSSSSSSSSSHK",
        "KHHHHHHHHHHHHHHK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a13": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KTTTTTTTTTTTTTTK",
        "KTSSSSSSSSSSSSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSKKKKKKKKKKSTK",
        "KTSSSSSSSSSSSSTK",
        "KTTTTTTTTTTTTTTK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "room_a14": {
    "idle": [
      [
        "KKKKKKKKKKKKKKKK",
        "KEEEEEEEEEEEEEEK",
        "KESSSSSSSSSSSSEK",
        "KESKKKKKKKKKKSEK",
        "KESKKKKKKKKKKSEK",
        "KESKKKKKKKKKKSEK",
        "KESKKKKKKKKKKSEK",
        "KESKKKKKKKKKKSEK",
        "KESKKKKKKKKKKSEK",
        "KESKKKKKKKKKKSEK",
        "KESKKKKKKKKKKSEK",
        "KESKKKKKKKKKKSEK",
        "KESKKKKKKKKKKSEK",
        "KESSSSSSSSSSSSEK",
        "KEEEEEEEEEEEEEEK",
        "KKKKKKKKKKKKKKKK"
      ]
    ]
  },
  "heart": {
    "idle": [
      [
        "................",
        "................",
        "................",
        "...KKK...KKK....",
        "..KRRRK.KRRRK...",
        ".KRWRRRKRRRRRK..",
        ".KRWRRRRRRRRRK..",
        ".KRRRRRRRRRRRK..",
        "..KRRRRRRRRRK...",
        "...KRRRRRRRK....",
        "....KRRRRRK.....",
        ".....KRRRK......",
        "......KRK.......",
        ".......K........",
        "................",
        "................"
      ]
    ]
  },
  "coin": {
    "idle": [
      [
        "................",
        "................",
        ".....KKKKKK.....",
        "....KYYYYYYK....",
        "...KYYWWYYYYK...",
        "...KYWYYYKYYK...",
        "...KYWYYKYYYK...",
        "...KYYYKYYYYK...",
        "...KYYYKYYYYK...",
        "...KYYKYYYWYK...",
        "...KYYYYYYWYK...",
        "....KYYYYYYK....",
        ".....KKKKKK.....",
        "................",
        "................",
        "................"
      ],
      [
        "................",
        "................",
        ".....KKKKKK.....",
        "....KYYYYYYK....",
        "...KYYYYYYYYK...",
        "...KYYYYYKYYK...",
        "...KYYYYKYYYK...",
        "...KYYYKYYYYK...",
        "...KYYYKYYYYK...",
        "...KYYKYYYYYK...",
        "...KYYYYYYYYK...",
        "....KYYYYYYK....",
        ".....KKKKKK.....",
        "................",
        "................",
        "................"
      ]
    ]
  },
  "stamina_pip": {
    "idle": [
      [
        "................",
        "................",
        "................",
        "......KKKK......",
        ".....KGGWGK.....",
        "....KGGGWGGK....",
        "...KGGGWGGGGK...",
        "...KGGWGGGGGK...",
        "...KGGGWGGGGK...",
        "...KGGGGWGGGK...",
        "....KGGGGGGK....",
        ".....KGGGGK.....",
        "......KKKK......",
        "................",
        "................",
        "................"
      ]
    ]
  }
});
